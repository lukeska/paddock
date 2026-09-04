package main

import (
	"fmt"
	"os"

	tea "charm.land/bubbletea/v2"

	"github.com/lukeska/paddock/internal/tui"
)

func main() {
	python := os.Getenv("PADDOCK_PYTHON")
	if python == "" {
		python = "/usr/bin/python"
	}
	program := tea.NewProgram(tui.New(python))
	final, err := program.Run()
	if model, ok := final.(tui.Model); ok && model.API() != nil {
		_ = model.API().Close()
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "paddock-tui:", err)
		os.Exit(1)
	}
}
