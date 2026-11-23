package main

import (
	"maps"
	"slices"
)

// Catalog explains the available controls, including the ones this tier locks.
// It is metadata only: authorization uses the filtered profile and tier policy.
type modelCatalog struct {
	ImageModels           []string                       `json:"imageModels"`
	ImageFeatures         map[string]imageFeatures       `json:"imageFeatures"`
	SVG                   svgSettings                    `json:"svg"`
	Reasoning             map[string]modelPreset         `json:"reasoning"`
	ReasoningModes        map[string]modelPreset         `json:"reasoningModes"`
	ReasoningCapabilities map[string]reasoningCapability `json:"reasoningCapabilities"`
}

func catalogForModels(settings *modelSettings) *modelCatalog {
	catalog := &modelCatalog{
		ImageModels: slices.Clone(settings.ImageModels), ImageFeatures: maps.Clone(settings.ImageFeatures), SVG: settings.SVG,
		Reasoning: maps.Clone(settings.Reasoning), ReasoningModes: maps.Clone(settings.ReasoningModes),
		ReasoningCapabilities: maps.Clone(settings.ReasoningCapabilities),
	}
	for model, features := range catalog.ImageFeatures {
		features.Quality = slices.Clone(features.Quality)
		catalog.ImageFeatures[model] = features
	}
	for model, capability := range catalog.ReasoningCapabilities {
		capability.Efforts = slices.Clone(capability.Efforts)
		catalog.ReasoningCapabilities[model] = capability
	}
	return catalog
}
