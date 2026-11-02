# Synthetic fixtures for Stage 31's adapters

Every file here was **written by us**, in the documented format of the source
it stands in for, by a throwaway script. None is a response from, or a
recording of, the real service: committing one would be that provider's data
in a public repository, which is the redistribution question D-136 leaves open
(D-142). The values are invented; the shapes are the providers'.

The night-lights granule is not here. It is written at test time with `h5py`
(`tests/unit/public_sources.py`), because an HDF5 file is binary and its
layout reads better as the code that builds it.

`celestrak-weather.tle` and `celestrak-no-data.txt` stand in for Stage 34's
element-set source (D-320). The three element sets were written field by field
with invented values and their checksums computed, in the column layout of the
two-line format; none is a set CelesTrak published.
