package client

import (
	"net/http"
)

type Client struct {
	http *http.Client
}

func (c *Client) Do(req *http.Request) (*http.Response, error) {
	return c.http.Do(req)
}
