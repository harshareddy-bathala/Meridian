import { describe, expect, it } from "vitest";

import { decodeApiReference } from "./apiReference";

const openapi = {
  openapi: "3.1.0",
  info: { title: "Meridian", version: "0.9.0" },
  paths: {
    "/msp/v0/heartbeat": { post: { summary: "Heartbeat" } },
    "/healthz": { get: { summary: "Healthz" } },
    "/api/v1/stations": {
      get: {
        summary: "List Stations",
        description: "Every registered station, oldest id first.\n\nArgs:\n  conn: ...",
      },
    },
    "/api/v1/datasets/{name}/{file}": {
      get: {
        summary: "Dataset File",
        description: "One file of one ``published`` package,\nas an attachment.",
      },
    },
  },
};

describe("the API reference", () => {
  it("lists every public read route, in the platform's order, by its first paragraph", () => {
    expect(decodeApiReference(openapi)).toEqual({
      version: "0.9.0",
      routes: [
        {
          path: "/api/v1/stations",
          summary: "List Stations",
          description: "Every registered station, oldest id first.",
        },
        {
          path: "/api/v1/datasets/{name}/{file}",
          summary: "Dataset File",
          description: "One file of one published package, as an attachment.",
        },
      ],
    });
  });

  it("refuses a description that is not one", () => {
    expect(() => decodeApiReference({ paths: {} })).toThrow("openapi.info");
  });
});
