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

type payload struct {
	PlayerId     string
	Dead         bool
	Tick         int
	Width        int
	Height       int
	FoodPosition vec2d
	Players      []playerPayload
}

type playerPayload struct {
	PlayerId     string
	HeadPosition vec2d
	Length       int
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
	conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{
		OriginPatterns: []string{"*"},
	})
	if err != nil {
		fmt.Printf("Accept error: %v\n", err)
		return
	}
	defer conn.CloseNow()

	// An errgroup manages both goroutines; if one fails, it cancels the shared context
	g, ctx := errgroup.WithContext(r.Context())

	player, err := s.createNewPlayer(conn)
	if err != nil {
		conn.Write(ctx, websocket.MessageText, []byte(err.Error()))
		return
	}
	s.players = append(s.players, &player)

	g.Go(func() error {
		return s.readLoop(ctx, conn, &player)
	})

	// Wait until either loop terminates (e.g., client disconnects or error occurs)
	if err := g.Wait(); err != nil && !errors.Is(err, context.Canceled) {
		if websocket.CloseStatus(err) != websocket.StatusNormalClosure &&
			websocket.CloseStatus(err) != websocket.StatusGoingAway {
			fmt.Printf("Connection error: %v\n", err)
		}
	}
}

// readLoop blocks on conn.Read, unblocking immediately when a new message arrives.
func (s *SnakeServer) readLoop(ctx context.Context, conn *websocket.Conn, player *player) error {
	for {
		messageType, payload, err := conn.Read(ctx)
		if err != nil {
			return fmt.Errorf("read error: %w", err)
		}

		if !s.acceptingCommands {
			continue
		}

		fmt.Printf("Received message (%v): %s\n", messageType, string(payload))

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

			for _, player := range s.players {
				s.updatePlayerPosition(player)
			}

			s.updateFood()

			var playerPayloads []playerPayload
			for _, player := range s.players {
				s.updateIsDead(player)
				if player.dead {
					continue
				}
				playerPayloads = append(playerPayloads, playerPayload{
					player.id.String(),
					player.tailPositions[0],
					len(player.tailPositions),
				})
			}

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

func (s *SnakeServer) updatePlayerPosition(player *player) {
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
	player.previousCommand = player.command
	newHead.X = newHead.X % s.Width
	newHead.Y = newHead.Y % s.Height

	if newHead.X < 0 {
		newHead.X = s.Width - 1
	}
	if newHead.Y < 0 {
		newHead.Y = s.Height - 1
	}

	// set the growth sport to where the last tail element was
	player.growthSpot = player.tailPositions[len(player.tailPositions)-1]

	// add head to start of array and remove the last element
	player.tailPositions = append([]vec2d{newHead}, player.tailPositions[:len(player.tailPositions)-1]...)
}

func (s *SnakeServer) updateIsDead(player *player) {
	head := player.tailPositions[0]
	for _, p := range s.players {
		for tailIdx, t := range p.tailPositions {
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

func (s *SnakeServer) updateFood() {
	var playersOnFood []*player
	for _, player := range s.players {
		head := player.tailPositions[0]
		if head.X == s.foodPos.X && head.Y == s.foodPos.Y {
			playersOnFood = append(playersOnFood, player)
		}
	}

	if len(playersOnFood) != 1 {
		return
	}

	player := playersOnFood[0]
	player.tailPositions = append(player.tailPositions, player.growthSpot)

	s.foodPos = vec2d{rand.Intn(s.Width), rand.Intn(s.Height)}
}

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

func (s *SnakeServer) getStartCellForNewPlayer(minSpace int) (vec2d, error) {
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

	var availableCells []vec2d
	for x := 0; x < s.Width; x++ {
		for y := 0; y < s.Height; y++ {
			cell := vec2d{x, y}
			if !blockedCells[cell] {
				availableCells = append(availableCells, cell)
			}
		}
	}

	if len(availableCells) == 0 {
		return vec2d{}, errors.New("no available cells for a new player")
	}

	return availableCells[rand.Intn(len(availableCells))], nil
}
