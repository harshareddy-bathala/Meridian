# Attribution

Every idea, algorithm or approach in this repository that was learned by reading another project's source or documentation is recorded here, in the same commit as the work it describes.

## Why this file exists

Three separate reasons, all of them serious:

1. **Licence.** The open ground station projects we learn from are licensed under GPL-3.0 and AGPL-3.0. Copying their source into this repository would impose those terms on us and require preserving their notices. We do not copy source. This file records that we read and reimplemented.
2. **Academic integrity.** Unacknowledged reuse in a submitted project is plagiarism, and consequences for it are more severe than for any technical shortfall.
3. **Defence.** In a viva, "did you copy this?" is a fair question. This file turns an accusation into a demonstration of professional practice. Its absence is what looks bad.

## Rules

- One entry per instance, added in the **same commit** as the work — never retrospectively.
- Record: date, what was read, its licence, what we wrote, and an explicit statement about whether any code was copied.
- Reading source to understand an approach and then writing our own implementation is normal engineering and is what this file documents.
- If code *is* ever copied, it must be recorded here, the licence obligations met, and the team lead informed before the commit lands.

## Format

```
YYYY-MM-DD  <topic>
            Read: <project / file / doc> (<licence>)
            Wrote: <what we implemented, and how it differs>
            Copied: none
```

## Dependencies

Libraries used as dependencies rather than reimplemented. Not attribution in the same sense, but recorded for completeness.

| Library | Licence | Used for |
|---|---|---|
| `sgp4` | MIT | Orbit propagation |
| `skyfield` | MIT | Coordinate frames, look angles |
| `highspy` (HiGHS) | MIT; bundled parts BSD-3, Apache-2.0, zlib, MIT | The scheduler's mixed-integer solver (D-167) |
| `h5py` | BSD-3-Clause | Reading HDF5 night-time lights granules, the `meridian-ingest[hdf5]` extra only (D-226) |
| Hamlib | LGPL-2.1 | Rotator control protocol |
| GNU Radio | GPL-3.0 | Demodulation (invoked as a separate process) |
| SatDump | GPL-3.0 | Decoding (invoked as a separate process) |
| FastAPI, SQLAlchemy, scikit-learn, NumPy, SciPy | MIT / BSD | Platform and modelling |
| Prometheus, Grafana | Apache-2.0 / AGPL-3.0 | Metrics and dashboards |
| IBM Plex Sans, IBM Plex Mono | SIL OFL-1.1 | Typography on the public site |

**Note on the fonts.** IBM Plex is the one dependency **vendored into this repository** rather than installed: three latin-subset `.woff2` files in `site/fonts/`, unmodified, taken from the `@ibm/plex-sans` and `@ibm/plex-mono` distributions on 2026-08-04. The site self-hosts them because it makes no third-party requests at runtime — a property its `Content-Security-Policy` enforces rather than asserts, and one a font CDN would break. OFL-1.1 permits redistribution provided the licence travels with the files and the Reserved Font Name is not applied to modified versions; `site/fonts/OFL.txt` ships alongside them and the files are bit-identical to upstream. No font was renamed, subsetted further or otherwise altered by us.

**Note on process boundaries.** GPL-licensed decoders are invoked as separate processes over defined interfaces, not linked into our code. This keeps our licensing decision independent.

**Licence decision, 2026-07-31.** Apache-2.0, recorded in `docs/DECISIONS.md`. Chosen deliberately, not inherited: permissive terms plus an explicit patent grant suit a published protocol and a reference client intended for third-party implementation. The process boundary above is what makes this available to us, and it must be maintained — linking a GPL decoder into our code would change the answer.

## Ingested data sources

Data we take rather than code we read, recorded here for the same reasons (D-134). An entry lands **before the first retrieval** from a source, not after it — the same rule the log below follows, applied to data.

**Licence and terms are recorded separately.** A licence says what the data is; terms of use often constrain redistribution independently of it, and that constraint is what decides whether the evidence dataset may republish a record or must reference it by identifier and checksum (D-104, D-136).

**Nothing has been retrieved yet.** Stage 14 built the ingest subsystem against a reference adapter of our own (D-142), and Stage 31 added an adapter per source class below, each exercised only against synthetic fixtures we wrote in the provider's documented format. No row below has been fetched from; each entry lands before the first retrieval, as D-134 requires.

**How the terms were read.** On 2026-09-29, while writing the adapters, from the provider's published statements as quoted by a search index: the build environment's network policy refused direct requests to every provider's host, so no page was fetched from its own server. Each entry names the page it summarises, and **an operator re-reads that page before the first live `meridian-ingest fetch` of the source** — a licence recorded from a quotation is a claim to verify, not a fact to rely on (D-220).

| Source class | Adopted source (entry name) | Used for | Access | Licence and terms |
|---|---|---|---|---|
| An **archive of amateur ground-station receptions** | none yet | Training and evaluation input, simulator outcome distributions, and Stage 16's observed count (D-138) | Varies by archive | *to record before first retrieval* |
| Published **geomagnetic and solar activity indices** | NOAA SWPC planetary K index | The disturbance feature (D-131) | none | US Government work. SWPC states its products carry no copyright or other restrictions; NOAA asks that unaltered data be attributed and that no endorsement be implied. Terms: https://www.swpc.noaa.gov/disclaimer |
| A **local atmospheric conditions** service | Open-Meteo cloud cover | Cloud cover, a candidate feature and a verdict input (D-131) | none; 600 a minute, 5 000 an hour, 10 000 a day per client | CC BY 4.0, with a link beside anything displayed. The free API is for non-commercial use only — public research at a public institution is named as such. Terms: https://open-meteo.com/en/terms |
| **Near-real-time global imagery tiles** | NASA GIBS imagery tiles | Display only — never sampled for a value (D-133) | none | NASA open data. GIBS asks for the acknowledgement: "We acknowledge the use of imagery provided by services from NASA's Global Imagery Browse Services (GIBS), part of NASA's Earth Observing System Data and Information System (EOSDIS)." Terms: https://nasa-gibs.github.io/gibs-api-docs/ |
| **Active fire detections** | NASA FIRMS active fire detections | Regional monitoring (Stage 32) | free `MAP_KEY`, 5 000 transactions per ten minutes | NASA open data: no restrictions on use; NASA asks to be cited and acknowledged as the source. Terms: https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance |
| **Vegetation index composites** | ORNL DAAC MODIS NDVI subsets | Regional monitoring | none | NASA open data through the ORNL DAAC, citation requested ("ORNL DAAC. MODIS Collection 6 Land Product Subsets Web Service. ORNL DAAC, Oak Ridge, Tennessee, USA"). Terms: https://daac.ornl.gov/LAND_VAL/guides/MODIS_Web_Service_C6_V2.html |
| **Precipitation** products | NASA POWER precipitation | Regional monitoring | none | No restrictions on use; the POWER Project asks for the acknowledgement: "The data was obtained from the National Aeronautics and Space Administration (NASA) Langley Research Center (LaRC) Prediction of Worldwide Energy Resource (POWER) Project funded through the NASA Earth Science/Applied Science Program." Terms: https://power.larc.nasa.gov/docs/services/api/ |
| **Aerosol** products | Open-Meteo aerosol optical depth (CAMS) | Regional monitoring, candidate context | none; the Open-Meteo limits above, shared with cloud cover | CC BY 4.0 from Open-Meteo, non-commercial free tier as above; the values are Copernicus Atmosphere Monitoring Service forecasts, credited to CAMS. Terms: https://open-meteo.com/en/terms |
| **Night-time lights** composites | NASA Black Marble night-time lights | Regional monitoring | registration: an Earthdata Login token | NASA open data: no restrictions on use, citation requested. Terms: https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance |
| A **national geoportal for India** | ISRO Bhuvan geoportal | **Display only**: map images behind Indian areas | none for viewing; registration for downloads | DOS/ISRO/NRSC grant a non-exclusive, non-transferable licence to access the portal; its image and map data are "provided for viewing purposes only", with no other use unless NRSC permits it. So no number is derived from it (D-220). Terms: https://bhuvan.nrsc.gov.in/wiki/index.php/Information_for_Users |

**What may be republished.** Nothing here is bundled into the repository: every fixture is synthetic. Whether any source's records may be republished inside the evidence dataset is D-136, still open; Open-Meteo's non-commercial condition and Bhuvan's viewing-only terms are the two that most constrain it.

**The reference adapter's fixtures are ours.** Stage 14's first adapter reads synthetic artefacts written by us for the purpose, not a recording of any real source, so nothing in this repository redistributes anyone's data and D-136 stays open until a real source is adopted (D-142).

**Keys and registration credentials are secrets** and appear nowhere in this repository, per `GIT-WORKFLOW.md` Rule 4. A source listed here and later dropped is removed rather than left implying a relationship we do not have.

## Log

**2026-08-08 — a note on how this log starts.** The four entries below were added
in one commit, retrospectively, which breaks the first rule above. They are
recorded rather than quietly skipped because the alternative is worse: an empty
log sitting beside `platform/src/meridian/store/pool.py`, whose module docstring
names the page it was written from. The lapse is the honest thing to defend — the
rule was written in Stage 0 and not carried into the habit of committing, and it
was an audit rather than review that noticed. From this entry forward the rule
binds normally: same commit, no exceptions. Nothing in the four was copied; each
was read, understood and written independently, which is exactly the case this
file exists to document.

```
2026-08-01  Foreign keys and unique indexes against a TimescaleDB hypertable
            Read: TimescaleDB 2.29 documentation and behaviour, verified by
                  running it (Apache-2.0 / Timescale License, mixed)
            Wrote: docs/DECISIONS.md D-015's correction note, and
                  docs/DATA-MODEL.md's natural-key rule. An earlier draft
                  rejected a supersedes_id pointer on the grounds that nothing
                  can hold a foreign key onto a hypertable. That was true
                  before 2.11 and is false on 2.29, which both creates and
                  enforces such a key. The decision did not change; the stated
                  reasoning did, from a false technical claim to the real
                  modelling argument. The companion finding — that a unique
                  index still requires the partitioning column, error quoted
                  verbatim in DATA-MODEL.md — is what forced the natural keys.
            Copied: none

2026-08-06  psycopg 3 connection pooling
            Read: psycopg 3 documentation, "Connection pools"
                  https://www.psycopg.org/psycopg3/docs/advanced/pool.html
                  (LGPL-3.0 project; documentation read, no source copied)
            Wrote: platform/src/meridian/store/pool.py — one pool opened in the
                  FastAPI lifespan, with min/max sizes derived from D-030's poll
                  interval and fifty stations rather than taken from any example,
                  and a `configure` hook setting the session time zone to UTC,
                  which the documentation does not suggest and DATA-MODEL.md
                  requires. The module docstring carries this citation inline.
            Copied: none

2026-08-06  Exponential backoff with full jitter
            Read: AWS Architecture Blog, "Exponential Backoff And Jitter"
                  (article, no licence attached to the prose; no code taken)
            Wrote: client/src/meridian_client/transport.py RetryPolicy —
                  `random.uniform(0, min(cap, base * 2**(n-1)))`. Full jitter
                  rather than backoff-plus-a-small-random-term, chosen because
                  it is the variant that decorrelates a fleet, which is the
                  property that matters at Phase 3's fifty simulated stations
                  and not the property the article was optimising for.
            Copied: none

2026-08-08  Reference values for topocentric look angles
            Read: Skyfield documentation, "Earth Satellites"
                  https://rhodesmill.org/skyfield/earth-satellites.html
                  (MIT)
            Wrote: tests/unit/test_look_angles_reference.py — the ISS element
                  set, the Bluffton ground site, the instant and the three
                  printed values (altitude, azimuth, distance) are transcribed
                  verbatim from that page's worked example and used as the
                  expected values our implementation is checked against. This is
                  a deliberate use of someone else's answer as the authority: a
                  propagator tested only against its own output is tested
                  against nothing. The implementation in
                  platform/src/meridian/orbit/skyfield_service.py is our own —
                  the sampler, the half-open window, the range-rate projection
                  and the sign convention appear nowhere in the source. The test
                  file states in its own docstring what this does not prove,
                  namely that a frame error inside Skyfield would be invisible
                  to a fixture derived from Skyfield.
            Copied: none — the TLE and the site are public data, and the printed
                  numbers are facts about them rather than expression.

2026-08-09  Cross-check for our own pass search
            Read: Skyfield API, `EarthSatellite.find_events` — its signature,
                  its `altitude_degrees` argument and its rise/culminate/set
                  event codes (MIT)
            Wrote: tests/unit/test_pass_windows_reference.py calls it and
                  compares its answer with ours, pass for pass. Only the calling
                  convention was read; the algorithm was not. Ours is a coarse
                  scan, bisection on elevation-minus-floor, and a ternary search
                  for the culmination, in platform/src/meridian/orbit/
                  pass_search.py and bracket_refinement.py — written before the
                  reference was consulted, which is why agreement between them
                  is evidence rather than a restatement. The test file records
                  that both sides share a propagator and what that costs.
            Copied: none

2026-08-09  SGP4 accuracy figures behind the timing-uncertainty prior
            Read: Vallado, Crawford, Hujsak & Kelso, "Revisiting Spacetrack
                  Report #3", AIAA 2006-6753 — the reported accuracy of SGP4
                  against precision ephemerides for low Earth orbit: about 1 km
                  near epoch, degrading by 1–3 km per day (paper, no licence
                  attached to the prose; no code taken)
            Wrote: platform/src/meridian/orbit/uncertainty.py turns those two
                  figures into a 1-sigma timing figure by dividing along-track
                  position error by orbital speed. The model, the choice to take
                  the top of the published growth range, and the decision to
                  report an along-track floor rather than an inflated guess are
                  ours and are recorded as D-060. No SGP4 implementation was
                  read or written — propagation is the sgp4/skyfield libraries'.
            Copied: none — two published measurements, cited
```
