package backend

import (
	"encoding/json"
	"io"
	"testing"
)

func TestClientUsesVersionedRequestsAndChecksResponseIDs(t *testing.T) {
	requestReader, requestWriter := io.Pipe()
	responseReader, responseWriter := io.Pipe()
	client := newClient(requestWriter, responseReader, nil)
	done := make(chan error, 1)
	go func() {
		defer responseWriter.Close()
		var req request
		if err := json.NewDecoder(requestReader).Decode(&req); err != nil {
			done <- err
			return
		}
		if req.ProtocolVersion != ProtocolVersion || req.Method != "snapshot.get" {
			done <- io.ErrUnexpectedEOF
			return
		}
		done <- json.NewEncoder(responseWriter).Encode(map[string]interface{}{
			"id": req.ID, "ok": true,
			"result": map[string]interface{}{"protocol_version": ProtocolVersion},
		})
	}()
	snapshot, err := client.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.ProtocolVersion != ProtocolVersion {
		t.Fatalf("version = %d", snapshot.ProtocolVersion)
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}

func TestClientTurnsStructuredErrorsIntoUsefulErrors(t *testing.T) {
	requestReader, requestWriter := io.Pipe()
	responseReader, responseWriter := io.Pipe()
	client := newClient(requestWriter, responseReader, nil)
	go func() {
		defer responseWriter.Close()
		var req request
		_ = json.NewDecoder(requestReader).Decode(&req)
		_ = json.NewEncoder(responseWriter).Encode(map[string]interface{}{
			"id": req.ID, "ok": false,
			"error": map[string]string{"code": "operation_failed", "message": "unit failed"},
		})
	}()
	_, err := client.SetActive("linguine", "queue", true)
	if err == nil || err.Error() != "operation_failed: unit failed" {
		t.Fatalf("unexpected error: %v", err)
	}
}
