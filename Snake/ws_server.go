package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math/rand"
	"net/http"
	"slices"
	"time"

	"github.com/beevik/guid"
	"github.com/coder/websocket"
	"golang.org/x/sync/errgroup"
)

type SnakeServer struct {
	Width             int
	Height            int
	Ticker            *time.Ticker
	currentTick       int
	players           []*player
	foodPos           vec2d
	acceptingCommands bool
}

type vec2d struct {
	X int
	Y int
}

type player struct {
	id              *guid.Guid
	conn            *websocket.Conn
	tailPositions   []vec2d
	growthSpot      vec2d
	command         string
	previousCommand string
	dead            bool
}

// the payload object that is sent to every player (connection)
type payload struct {
	// the players own id
	PlayerId     string
	// whether the player died
	Dead         bool
	// the current tick (incrementing)
	Tick         int
	// width of the game field
	Width        int
	// height of the game field
	Height       int
	FoodPosition vec2d
	Players      []playerPayload
}

// the object with information about every player
type playerPayload struct {
	// id of the player
	PlayerId      string
	// position of the head
	HeadPosition  vec2d
	// positions of the tail (including the head)
	TailPositions []vec2d
	// length of the tail (including head)
	Length        int
}

var CMD_LEFT = "LEFT"
var CMD_RIGHT = "RIGHT"
var CMD_UP = "UP"
var CMD_DOWN = "DOWN"

func CreateServer(width int, height int, ticker *time.Ticker) *SnakeServer {
	return &SnakeServer{
		Width:             width,
		Height:            height,
		Ticker:            ticker,
		acceptingCommands: true,
		currentTick:       0,
		players:           []*player{},
		foodPos:           vec2d{rand.Intn(width), rand.Intn(height)},
	}
}

func (s *SnakeServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// accept a connection
	conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{
		OriginPatterns: []string{"*"},
	})
	if err != nil {
		fmt.Printf("Accept error: %v\n", err)
		return
	}
	defer conn.CloseNow()

	g, ctx := errgroup.WithContext(r.Context())

	// create the player and append it to the player array
	player, err := s.createNewPlayer(conn)
	if err != nil {
		conn.Write(ctx, websocket.MessageText, []byte(err.Error()))
		return
	}
	s.players = append(s.players, &player)

	// start the read loop for the player
	g.Go(func() error {
		return s.readLoop(ctx, conn, &player)
	})

	if err := g.Wait(); err != nil && !errors.Is(err, context.Canceled) {
		if websocket.CloseStatus(err) != websocket.StatusNormalClosure &&
			websocket.CloseStatus(err) != websocket.StatusGoingAway {
			fmt.Printf("Connection error: %v\n", err)
		}
	}
}

// one read loop for each player
func (s *SnakeServer) readLoop(ctx context.Context, conn *websocket.Conn, player *player) error {
	for {
		// read the message sent from the player
		messageType, payload, err := conn.Read(ctx)
		if err != nil {
			return fmt.Errorf("read error: %w", err)
		}

		// prohibits changing commands during the write loop
		if !s.acceptingCommands {
			continue
		}

		fmt.Printf("Received message (%v): %s\n", messageType, string(payload))

		// Validate the command and skip if invalid
		message := string(payload)
		if message != CMD_DOWN && message != CMD_UP && message != CMD_RIGHT && message != CMD_LEFT {
			continue
		}
		if (message == CMD_UP && player.previousCommand == CMD_DOWN) ||
			(message == CMD_DOWN && player.previousCommand == CMD_UP) ||
			(message == CMD_RIGHT && player.previousCommand == CMD_LEFT) ||
			(message == CMD_LEFT && player.previousCommand == CMD_RIGHT) {
			continue
		}

		player.command = message
	}
}

// one write loop for all players
func (s *SnakeServer) StartWriteLoop(ctx context.Context) error {
	fmt.Println("StartWriteLoop")
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case t := <-s.Ticker.C:
			s.currentTick++
			s.acceptingCommands = false
			fmt.Println("Write loop at tick", s.currentTick, "at time", t.String(), "- player count: ", len(s.players))

			// update each players position based on their current command
			for _, player := range s.players {
				s.updatePlayerPosition(player)
			}

			// update the food - if it is eaten, a new one spawns and the player that ate it gets longer
			s.updateFood()

			// create the payload for each player
			var playerPayloads []playerPayload
			for _, player := range s.players {
				// check whether the player died
				s.updateIsDead(player)
				if player.dead {
					continue
				}
				playerPayloads = append(playerPayloads, playerPayload{
					PlayerId:      player.id.String(),
					HeadPosition:  player.tailPositions[0],
					TailPositions: slices.Clone(player.tailPositions),
					Length:        len(player.tailPositions),
				})
			}

			// send a payload to each player
			for _, player := range s.players {
				payload := payload{
					PlayerId:     player.id.String(),
					Dead:         player.dead,
					Tick:         s.currentTick,
					FoodPosition: s.foodPos,
					Players:      playerPayloads,
					Width:        s.Width,
					Height:       s.Height,
				}

				writer, err := player.conn.Writer(ctx, websocket.MessageText)
				if err != nil {
					fmt.Println("Failed to open writer for player", player.id.String(), "Removing player.")
					player.conn.CloseNow()
					player.dead = true
					continue
				}
				fmt.Println("Sending payload")
				err = json.NewEncoder(writer).Encode(payload)
				writer.Close()
				if err != nil {
					fmt.Println(err.Error())
				}

				// close the connection if the player has died
				if player.dead {
					err := player.conn.Close(websocket.StatusTryAgainLater, "You are dead.")
					if err != nil {
						fmt.Println(err.Error())
					}
				}
			}

			s.players = slices.DeleteFunc(s.players, func(p *player) bool {
				return p.dead
			})

			s.acceptingCommands = true
		}
	}
}

// update each players tail positions
func (s *SnakeServer) updatePlayerPosition(player *player) {
	// the new head positions is the old head position shifted by the command
	oldHead := player.tailPositions[0]
	newHead := vec2d{oldHead.X, oldHead.Y}
	fmt.Println("Update player position with command", player.command)
	switch player.command {
	case CMD_UP:
		newHead.Y++
	case CMD_DOWN:
		newHead.Y--
	case CMD_RIGHT:
		newHead.X++
	case CMD_LEFT:
		newHead.X--
	}
	// save the command in previous command to validate new commands in the read loop
	player.previousCommand = player.command

	// wrap around at game field borders
	newHead.X = newHead.X % s.Width
	newHead.Y = newHead.Y % s.Height
	if newHead.X < 0 {
		newHead.X = s.Width - 1
	}
	if newHead.Y < 0 {
		newHead.Y = s.Height - 1
	}

	// set the growth spot (used when food is eaten) to where the last tail element was
	player.growthSpot = player.tailPositions[len(player.tailPositions)-1]

	// add head to start of array and remove the last element
	player.tailPositions = append([]vec2d{newHead}, player.tailPositions[:len(player.tailPositions)-1]...)
}

// check whether the players head is at an already occupied cell. If so -> dead
func (s *SnakeServer) updateIsDead(player *player) {
	head := player.tailPositions[0]
	// loop through all players and all tail positions of each player
	for _, p := range s.players {
		for tailIdx, t := range p.tailPositions {
			// skip the own head (own head is of course always on the same cell as the own head)
			if p.id.String() == player.id.String() && tailIdx == 0 {
				continue
			}
			if t.X == head.X && t.Y == head.Y {
				player.dead = true
				return
			}
		}
	}
}

// if a players head is on the food, this player gets longer (grows onto the growth spot) and the food reappears at a random position
func (s *SnakeServer) updateFood() {
	// check which players are on the food
	var playersOnFood []*player
	for _, player := range s.players {
		head := player.tailPositions[0]
		if head.X == s.foodPos.X && head.Y == s.foodPos.Y {
			playersOnFood = append(playersOnFood, player)
		}
	}

	// 0 -> no player on the food
	// > 1 -> multiple players on the food -> they have collided and will die, so the food stays
	if len(playersOnFood) != 1 {
		return
	}

	// grow the tail on the growth spot
	player := playersOnFood[0]
	player.tailPositions = append(player.tailPositions, player.growthSpot)

	// regenerate food position
	s.foodPos = vec2d{rand.Intn(s.Width), rand.Intn(s.Height)}
}

// creates a new player at a random position (not directly immediate to another player) with a default command
func (s *SnakeServer) createNewPlayer(conn *websocket.Conn) (player, error) {
	startCell, err := s.getStartCellForNewPlayer(2)
	if err != nil {
		return player{}, err
	}

	p := player{
		id:              guid.New(),
		conn:            conn,
		tailPositions:   []vec2d{startCell},
		growthSpot:      vec2d{startCell.X + 1, startCell.Y},
		command:         CMD_RIGHT,
		previousCommand: CMD_RIGHT,
		dead:            false,
	}

	fmt.Println("Created new player", p.id.String(), "at (", startCell.X, "|", startCell.Y, ")")

	return p, nil
}

// get a possible random start cell for a player that has at least minSpace clearance to other players
func (s *SnakeServer) getStartCellForNewPlayer(minSpace int) (vec2d, error) {
	// all cells from -minSpace to minSpace around another players tail are blocked
	blockedCells := map[vec2d]bool{}
	for _, player := range s.players {
		for _, tailPosition := range player.tailPositions {
			for dx := -minSpace; dx <= minSpace; dx++ {
				for dy := -minSpace; dy <= minSpace; dy++ {
					blockedCells[vec2d{tailPosition.X + dx, tailPosition.Y + dy}] = true
				}
			}
		}
	}

	// get all available cells that are not blocked
	var availableCells []vec2d
	for x := 0; x < s.Width; x++ {
		for y := 0; y < s.Height; y++ {
			cell := vec2d{x, y}
			if !blockedCells[cell] {
				availableCells = append(availableCells, cell)
			}
		}
	}

	// if no cells are available the player can not spawn
	if len(availableCells) == 0 {
		return vec2d{}, errors.New("no available cells for a new player")
	}

	return availableCells[rand.Intn(len(availableCells))], nil
}
