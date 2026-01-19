import test from "node:test";
import assert from "node:assert/strict";

import {
  bandClass,
  normalizePercentile,
  ratingBand,
  ratingScore,
  trendDirection,
} from "../src/domain/ratings.js";

test("normalizePercentile accepts percent or fraction", () => {
  assert.equal(normalizePercentile(0.5), 0.5);
  assert.equal(normalizePercentile(50), 0.5);
  assert.equal(normalizePercentile(null), null);
});

test("ratingScore uses weights with percent or fraction inputs", () => {
  const scoreFraction = ratingScore(0.8, 0.2);
  const scorePercent = ratingScore(80, 20);
  assert.equal(scoreFraction, scorePercent);
  assert.equal(scoreFraction, Number(((0.7 * 0.8 + 0.3 * 0.2) * 100).toFixed(1)));
});

test("ratingBand thresholds", () => {
  assert.equal(ratingBand(null), "Unknown");
  assert.equal(ratingBand(90), "High");
  assert.equal(ratingBand(72), "Elevated");
  assert.equal(ratingBand(56), "Watch");
  assert.equal(ratingBand(40), "Stable");
});

test("trendDirection thresholds", () => {
  assert.equal(trendDirection(null), "flat");
  assert.equal(trendDirection(0.1), "up");
  assert.equal(trendDirection(-0.1), "down");
  assert.equal(trendDirection(0.0), "flat");
});

test("bandClass mapping", () => {
  assert.equal(bandClass("high"), "band-high");
  assert.equal(bandClass("Elevated"), "band-elevated");
  assert.equal(bandClass("watch"), "band-watch");
  assert.equal(bandClass("stable"), "band-stable");
  assert.equal(bandClass("unknown"), "band-unknown");
});
