// Build-time settings the dashboard reads, typed so a typo is a compile error.

interface ImportMetaEnv {
  /**
   * A Leaflet tile URL template. Unset uses OpenStreetMap's standard tiles; an
   * empty string builds a map with no third-party request at all (D-092).
   */
  readonly VITE_MAP_TILE_URL?: string;
}
