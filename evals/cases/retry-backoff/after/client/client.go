package client

import (
	"context"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"time"
)

type Client struct {
	http       *http.Client
	maxRetries int
	base       time.Duration
}

func retryable(method string) bool {
	switch method {
	case http.MethodGet, http.MethodHead, http.MethodPut, http.MethodDelete:
		return true
	}
	return false
}

func (c *Client) Do(req *http.Request) (*http.Response, error) {
	var lastErr error
	for attempt := 0; ; attempt++ {
		resp, err := c.http.Do(req)
		if err == nil && resp.StatusCode < 500 {
			return resp, nil
		}
		if err == nil {
			io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
			lastErr = fmt.Errorf("server returned %d", resp.StatusCode)
		} else {
			lastErr = err
		}
		if attempt >= c.maxRetries || !retryable(req.Method) {
			return nil, lastErr
		}
		sleep := c.base*time.Duration(1<<attempt) + time.Duration(rand.Int63n(int64(c.base)))
		select {
		case <-req.Context().Done():
			return nil, fmt.Errorf("%w: %v", req.Context().Err(), lastErr)
		case <-time.After(sleep):
		}
	}
}

var _ = context.Canceled
