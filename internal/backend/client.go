package backend

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"sync"
)

// Moves with tui_bridge.PROTOCOL_VERSION. Both sides check for equality
// rather than a minimum, because the bridge and this client ship in the
// same package and a mismatch means a broken install, not an old peer.
const ProtocolVersion = 6

type rpcError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

type response struct {
	ID     int             `json:"id"`
	OK     bool            `json:"ok"`
	Result json.RawMessage `json:"result"`
	Error  *rpcError       `json:"error"`
}

type request struct {
	ProtocolVersion int         `json:"protocol_version"`
	ID              int         `json:"id"`
	Method          string      `json:"method"`
	Params          interface{} `json:"params"`
}

// Client serializes requests because the bridge is intentionally one small,
// ordered process rather than a long-running public API.
type Client struct {
	mu      sync.Mutex
	nextID  int
	encoder *json.Encoder
	decoder *json.Decoder
	stdin   io.WriteCloser
	cmd     *exec.Cmd
}

func Start(python string) (*Client, error) {
	cmd := exec.Command(python, "-m", "paddock.tui_bridge")
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		_ = stdin.Close()
		return nil, err
	}
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		_ = stdin.Close()
		return nil, err
	}
	return newClient(stdin, stdout, cmd), nil
}

func newClient(stdin io.WriteCloser, stdout io.Reader, cmd *exec.Cmd) *Client {
	return &Client{
		nextID:  1,
		encoder: json.NewEncoder(stdin),
		decoder: json.NewDecoder(bufio.NewReader(stdout)),
		stdin:   stdin,
		cmd:     cmd,
	}
}

func (c *Client) call(method string, params interface{}, result interface{}) error {
	c.mu.Lock()
	defer c.mu.Unlock()

	id := c.nextID
	c.nextID++
	if err := c.encoder.Encode(request{ProtocolVersion, id, method, params}); err != nil {
		return fmt.Errorf("send bridge request: %w", err)
	}
	var reply response
	if err := c.decoder.Decode(&reply); err != nil {
		return fmt.Errorf("read bridge response: %w", err)
	}
	if reply.ID != id {
		return fmt.Errorf("bridge response id %d did not match request %d", reply.ID, id)
	}
	if !reply.OK {
		if reply.Error == nil {
			return errors.New("bridge returned an unspecified error")
		}
		return fmt.Errorf("%s: %s", reply.Error.Code, reply.Error.Message)
	}
	if result == nil {
		return nil
	}
	if err := json.Unmarshal(reply.Result, result); err != nil {
		return fmt.Errorf("decode bridge result: %w", err)
	}
	return nil
}

func (c *Client) Snapshot() (Snapshot, error) {
	var result Snapshot
	err := c.call("snapshot.get", map[string]interface{}{}, &result)
	if err == nil && result.ProtocolVersion != ProtocolVersion {
		err = fmt.Errorf(
			"bridge protocol %d is incompatible with terminal UI protocol %d",
			result.ProtocolVersion, ProtocolVersion,
		)
	}
	return result, err
}

func (c *Client) SetActive(site, worker string, active bool) (OperationResult, error) {
	var result OperationResult
	err := c.call("worker.set_active", map[string]interface{}{
		"site": site, "worker": worker, "active": active,
	}, &result)
	return result, err
}

func (c *Client) SetDashboardActive(active bool) (DashboardOperationResult, error) {
	var result DashboardOperationResult
	err := c.call("dashboard.set_active", map[string]interface{}{
		"active": active,
	}, &result)
	return result, err
}

func (c *Client) InstallPHP(minor string) (PHPInstallResult, error) {
	var result PHPInstallResult
	err := c.call("php.install", map[string]interface{}{"minor": minor}, &result)
	return result, err
}

func (c *Client) InstallNode(major string) (NodeInstallResult, error) {
	var result NodeInstallResult
	err := c.call("node.install", map[string]interface{}{"major": major}, &result)
	return result, err
}

func (c *Client) AddParkingPath(path string) (ParkingOperationResult, error) {
	var result ParkingOperationResult
	err := c.call("parking.add", map[string]interface{}{"path": path}, &result)
	return result, err
}

func (c *Client) RemoveParkingPath(path string) (ParkingOperationResult, error) {
	var result ParkingOperationResult
	err := c.call("parking.remove", map[string]interface{}{"path": path}, &result)
	return result, err
}

func (c *Client) CreateService(kind, label string, port *int, autostart bool) (ServiceOperationResult, error) {
	var result ServiceOperationResult
	err := c.call("service.create", map[string]interface{}{
		"type": kind, "label": label, "port": port, "autostart": autostart,
	}, &result)
	return result, err
}

func (c *Client) SetServiceActive(id string, active bool) (ServiceOperationResult, error) {
	var result ServiceOperationResult
	err := c.call("service.set_active", map[string]interface{}{"id": id, "active": active}, &result)
	return result, err
}

func (c *Client) UpdateService(id, label string, port int, autostart bool) (ServiceOperationResult, error) {
	var result ServiceOperationResult
	err := c.call("service.update", map[string]interface{}{
		"id": id, "label": label, "port": port, "autostart": autostart,
	}, &result)
	return result, err
}

func (c *Client) RemoveService(id string) (ServiceOperationResult, error) {
	var result ServiceOperationResult
	err := c.call("service.remove", map[string]interface{}{"id": id}, &result)
	return result, err
}

func (c *Client) ServiceLogs(id string, lines int) (ServiceLogsResult, error) {
	var result ServiceLogsResult
	err := c.call("service.logs", map[string]interface{}{"id": id, "lines": lines}, &result)
	return result, err
}

func (c *Client) SetAutostart(site, worker string, enabled bool) (OperationResult, error) {
	var result OperationResult
	err := c.call("worker.set_autostart", map[string]interface{}{
		"site": site, "worker": worker, "enabled": enabled,
	}, &result)
	return result, err
}

func (c *Client) SetSitePHP(site, version string) (OperationResult, error) {
	var result OperationResult
	err := c.call("site.set_php", map[string]interface{}{
		"site": site, "version": version,
	}, &result)
	return result, err
}

func (c *Client) SetSiteNode(site, version string) (OperationResult, error) {
	var result OperationResult
	err := c.call("site.set_node", map[string]interface{}{
		"site": site, "version": version,
	}, &result)
	return result, err
}

func (c *Client) SetSiteSecured(site string, secured bool) (OperationResult, error) {
	var result OperationResult
	err := c.call("site.set_secured", map[string]interface{}{
		"site": site, "secured": secured,
	}, &result)
	return result, err
}

func (c *Client) SetSiteConfigurationTrusted(site string, trusted bool) (OperationResult, error) {
	var result OperationResult
	err := c.call("site.set_configuration_trusted", map[string]interface{}{
		"site": site, "trusted": trusted,
	}, &result)
	return result, err
}

func (c *Client) EnsureSiteConfiguration(site string) (OperationResult, error) {
	var result OperationResult
	err := c.call("site.ensure_configuration", map[string]interface{}{
		"site": site,
	}, &result)
	return result, err
}

func (c *Client) ReloadWeb() (DashboardOperationResult, error) {
	var result DashboardOperationResult
	err := c.call("web.reload", map[string]interface{}{}, &result)
	return result, err
}

func (c *Client) Logs(site, worker string, lines int) (LogsResult, error) {
	var result LogsResult
	err := c.call("worker.logs", map[string]interface{}{
		"site": site, "worker": worker, "lines": lines,
	}, &result)
	return result, err
}

func (c *Client) Close() error {
	if c == nil {
		return nil
	}
	_ = c.stdin.Close()
	if c.cmd != nil {
		return c.cmd.Wait()
	}
	return nil
}
