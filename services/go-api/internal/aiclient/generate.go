// Package aiclient is the generated HTTP client for the ai-service internal API.
//
// The source of truth is api/openapi/internal-ai.yaml (ADR-005). Do not edit
// aiclient.gen.go by hand: change the contract and run `make generate`; CI fails
// when the committed file is out of date.
package aiclient

//go:generate go tool oapi-codegen -config oapi-codegen.yaml ../../../../api/openapi/internal-ai.yaml
