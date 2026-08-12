# The local catalogue

What this deployment can see: the satellites it tracks, the downlinks it expects
to hear, and the element sets that make them predictable. Loaded with

```sh
meridian catalogue load --file deploy/catalogue/development.json
```

Loading is idempotent — running it twice writes nothing the second time — so it
is safe in a startup script and safe to re-run after editing. See
`docs/DECISIONS.md` D-079 for why the catalogue is a local file rather than a
fetch from an element-set provider.

## `development.json` is not measurement-grade

**Read this before quoting any number produced from it.**

The two satellites are real — Meteor-M2-3 and Meteor-M2-4, with their real NORAD
ids and their real LRPT downlinks at 137.1 and 137.9 MHz. The **element sets are
not retrieved**. They carry representative sun-synchronous elements at a fixed
epoch, which is enough to make the simulator's passes real passes over a real
sky, and is not enough to point an antenna at anything.

Two consequences follow:

- The sets are stamped `source: manual`, never `celestrak`, so nothing in the
  archive claims a provenance it does not have. Element-set age is a first-class
  feature everywhere in this project, and this file ages from a fixed epoch —
  every day it sits here it gets staler, and the predictions from it get worse.
- **Replace it before receiving anything.** For a station that is actually
  listening, write a catalogue whose element sets came from an element-set
  provider on the day they are used.

## Adding your own

One JSON object with a `satellites` array. Every field:

```json
{
  "satellites": [
    {
      "satellite_id": "norad:57166",
      "name": "Meteor-M2-3",
      "orbital_regime": "leo",
      "transmitters": [
        {
          "centre_freq_hz": 137100000,
          "mode": "lrpt",
          "polarisation": "rhcp",
          "bandwidth_hz": 150000
        }
      ],
      "element_sets": [
        { "line1": "1 57166U ...", "line2": "2 57166 ..." }
      ]
    }
  ]
}
```

`orbital_regime` defaults to `leo`. `polarisation` and `bandwidth_hz` are
optional. An element set states no epoch: it is read out of line 1, because the
epoch belongs to the set rather than to whoever transcribed it.

Both element-set lines are checked for width, line number and the modulo-10
checksum before anything is written. That check is not decoration — the
propagator accepts two lines of arbitrary text and returns an epoch in 1999, so
an unvalidated typo would load quietly and then produce no passes for a reason
nobody could find.

A station only ever receives a pass for a transmitter its declared capabilities
cover, so a frequency added here reaches nobody until some station reports being
able to hear it.
