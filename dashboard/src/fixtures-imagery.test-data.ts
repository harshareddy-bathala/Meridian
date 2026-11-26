// What the imagery pages read, for the tests that draw them.

export const HASH = "0123456789ab";

export function sceneItem(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    hash: HASH,
    area_id: 1,
    area_label: "Bengaluru urban",
    source_class: "high_resolution",
    source_id: "sentinel2_l2a",
    platform: "Sentinel-2B",
    instrument: "MSI",
    acquired_at: "2026-11-16T05:21:00Z",
    published_at: "2026-11-18T06:00:00Z",
    cloud_fraction: 0.04,
    caption: "High-resolution satellite image of Bengaluru urban.",
    credit: "Contains modified Copernicus Sentinel data 2026",
    display: `/imagery/${HASH}/display.png`,
    size_bytes: 2_400_000,
    simulated: false,
    ...overrides,
  };
}

export const imageryBody = { status: "computed", items: [sceneItem()], next_cursor: null };

const areaCitation = {
  source_id: "meridian_areas",
  record_id: "areas-summary-1 of report aaaaaaaaaaaa, area 1",
  url: "/api/v1/areas",
  licence: "Meridian's published areas summary",
};

export const annotations = [
  {
    kind: "area",
    geometry: { type: "box", bbox: [77.58, 12.96, 77.6, 12.98] },
    label: "Bengaluru urban",
    text: "Bengaluru urban: the published area, drawn as its bounding box",
    citations: [areaCitation],
  },
  {
    kind: "fire",
    geometry: { type: "point", lat_deg: 12.97, lon_deg: 77.59 },
    label: "Fire",
    text: "Active fire detected 16 Nov 2026 05:30 UTC by VIIRS on Suomi NPP",
    citations: [
      {
        source_id: "nasa_firms",
        record_id: "0123456789ab row 1",
        url: "https://firms.modaps.eosdis.nasa.gov/",
        licence: "NASA open data",
      },
    ],
  },
  {
    kind: "place",
    geometry: { type: "point", lat_deg: 12.975, lon_deg: 77.585 },
    label: "Aranpur <b>",
    text: "Aranpur <b>, a populated place of 840,000 people as GeoNames lists it",
    citations: [
      {
        source_id: "geonames_cities1000",
        record_id: "geonameid 9100001",
        url: "https://www.geonames.org/9100001",
        licence: "CC BY 4.0",
      },
    ],
  },
];

const file = (path: string, size: number) => ({ path, sha256: "1".repeat(64), size_bytes: size });

export function sceneBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    status: "computed",
    hash: HASH,
    published_at: "2026-11-18T06:00:00Z",
    base: `/imagery/${HASH}/`,
    simulated: false,
    manifest: {
      schema: "imagery-manifest-1",
      method: "imagery-1",
      area_id: 1,
      area_label: "Bengaluru urban",
      source_id: "sentinel2_l2a",
      source_class: "high_resolution",
      platform: "Sentinel-2B",
      instrument: "MSI",
      acquired_at: "2026-11-16T05:21:00Z",
      bounds: [77.58, 12.96, 77.6, 12.98],
      crs: "EPSG:4326",
      pixel_size_deg: [0.0001, 0.0001],
      resolution_m: 10,
      cloud_fraction: 0.04,
      cloud_method: "as counted from the product's scene classification layer (SCL)",
      rendering: "the visual asset as published",
      zoom_min: 11,
      zoom_max: 14,
      credit: "Contains modified Copernicus Sentinel data 2026",
      licence: "Copernicus free, full and open",
      caption: "High-resolution satellite image of Bengaluru urban.",
      caption_sha256: "2".repeat(64),
      caption_template: "imagery-caption-1",
      annotations,
      inputs: [{ kind: "template", ref: "imagery-caption-1", sha256: "f".repeat(64) }],
      files: [
        file("display.png", 900_000),
        file("scene.pgw", 120),
        file("scene.png", 700_000),
        file("scene.tif", 1_500_000),
        file("tiles/14/11727/7594.webp", 30_000),
      ],
    },
    ...overrides,
  };
}
