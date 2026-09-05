# opentpt — the TPT measurement engine

The headless engine. A run is a pure function of one recipe file, so the bench
no longer needs an assistant in the loop. TPT Studio (`opentpt_studio/`) is a
renderer on top of it, not a second implementation.

```
venv\Scripts\python.exe -m opentpt validate recipes/tx26_3c90.recipe.json
venv\Scripts\python.exe -m opentpt run      recipes/tx26_3c90.recipe.json
venv\Scripts\python.exe -m opentpt analyse  coreloss_25kHz_200mT.csv -f 25e3
venv\Scripts\python.exe -m opentpt replay   recipes/tx26_3c90.recipe.json \
    --capture 25e3=coreloss_25kHz_200mT.csv --capture 100e3=coreloss_100kHz_100mT.csv
```

`validate` plans every point without touching hardware. `replay` runs the whole
engine against stored captures — that is how the tests exercise it in CI.
`analyse` re-derives one stored waveform, which is the practical form of the
reproducibility claim: any number in a dataset can be recomputed from its
sidecar.

## Layout

| Module | Role |
|---|---|
| `analysis.py` | pure numpy reductions — cycle location, B/H, loss, all permeabilities |
| `qc.py` | the gates, and the thresholds a recipe can set |
| `recipe.py` | the run definition, geometry lookup, safe procedure ordering |
| `drive.py` | flux↔voltage, pulse trains, feasibility refusal |
| `events.py` | the event stream and the JSONL journal |
| `bench.py` | instruments (`HardwareBench`) and stored captures (`ReplayBench`) |
| `procedures/` | one module per procedure type |
| `engine.py` | the state machine over the recipe |
| `mas.py` | MAS `coreMaterial` writer + flat points CSV |
| `omdb.py` | optional bridge to the OpenMagnetics catalogue (geometry + datasheet fields) |
| `resume.py` | recovering accepted points from a prior run's journal |
| `accept.py` | run-twice repeatability comparison |
| `paths.py` | resource vs. working-directory roots (matters once packaged) |
| `mas_schema.py` | validation against the vendored MAS JSON schema |
| `cli.py` | the five commands above |

Only `bench` and `procedures` touch instruments. Everything above `analysis` is
testable with no hardware attached.

## Output

A run writes, into `<export.directory>/<name>/`:

- `<name>.mas.json` — the MAS `coreMaterial` document, **validated against the
  real schema on every write**. Only points that passed every gate reach it.
- `<name>.provenance.json` — recipe, bench checks, per-procedure results,
  caveats, and the schema-validation report. A *sidecar*, because the MAS root
  sets `additionalProperties: false` and an `_opentpt` block would make the
  document invalid.
- `<name>_points.csv` — every attempt, rejects included and labelled with the
  gate that failed. This is what makes a gap in the dataset explainable.
- `journal.jsonl` — every event: each retry, each verdict, each skip.
- `recipe.used.json` — the recipe as executed.
- `waveforms/*.csv` — raw captures, when `export.save_waveforms` is set.

Check a dataset at any time:

```
venv\Scripts\python.exe -m opentpt validate-dataset datasets/tx26_3c90/tx26_3c90.mas.json
```

Exit code 0/1, so it drops into CI. The schema is vendored under
`schemas/mas/` (Apache-2.0; `VERSION.txt` records the upstream commit) so
validation works offline and against the schema a dataset was written for.

### How the document is built

It starts from the **OpenMagnetics database record** for the material and
overlays what we measured. That is not a shortcut — MAS *requires*
`resistivity`, `saturation` and `manufacturerInfo`, none of which this bench
can measure. Starting from the catalogue record means they carry their real
datasheet values instead of being invented or omitted.

Slots we measured are **replaced, not merged**: `permeabilityPoint` has no
`origin` field, so a datasheet point and a measured point in the same array
would be indistinguishable. Replacing keeps every array single-sourced, and
the provenance file records which slots became measurements.

Measured loss points live in a `roshen` entry's `referenceVolumetricLosses`.
That is the only array of `volumetricLossesPoint` the `coreMaterial` schema
provides, so it is the conformant home for raw measured points — not a claim
that a Roshen model was fitted. The provenance file says so too.

## Five things the reductions get right that the old scripts did not

1. **Sense-winding polarity is resolved from physics, not assumed.** `V_sec` is
   wired anti-phase with the primary on this bench. The old `abs()` around Q
   hid it; here the sign of `∮i·v_sec dt` fixes the orientation (a passive core
   cannot generate energy), which keeps B, H, µ′, µ″, B_r and H_c mutually
   consistent and surfaces the wiring as a reported flag.

2. **The ringdown cycle is rejected.** The last rise-to-rise window of a record
   often closes inside the post-train LC ringdown. On `coreloss_100kHz_100mT`
   that window runs 11 % long and reduces to 5.07 % imbalance / 116 mT; the
   clean cycle before it is 1.16 % / 107 mT. A fixed tolerance around 1/f
   cannot separate the two (the deadtime legitimately lengthens the period by
   ~1 % at 25 kHz and ~20 % at 200 kHz), so the filter is the *median* period of
   the burst — every real cycle agrees, contamination does not.

3. **Loss uses the measured repetition rate.** `P = Q / period_measured`, not
   `Q · f_nominal`. The deadtime sits between pulses, so the real period is
   longer and the nominal rate overstates Pv.

4. **Remanence and coercivity are taken about the loop's mid-swing.** The
   current channel carries a residual DC offset, so measuring against absolute
   zero put B_r at 82 % of B̂ on a capture whose loss angle implies ~23 %.

5. **Infeasible points are refused, never clamped.** A point silently run at a
   lower voltage than it asked for is a real measurement of the wrong operating
   point — worse than a labelled gap.

## Known caveats, carried into every dataset

- `current_probe_scale` is **uncalibrated**. Every absolute watt figure scales
  linearly with it. Calibrate against a known resistor (V/I must equal R)
  before publishing absolutes. Shapes and ratios are unaffected.
- Ambient temperature only. Every point is recorded at the DUT's nominal
  ambient; a heated plate is the highest-value hardware addition.
- Remanence and coercive force are **dynamic** — AC loop crossings at the
  stated frequency, not DC catalogue values.
- `complex_mu_spectrum` is the **experimental** tier and says so in its output.
  An impedance analyser remains the reference method.
- **Four subtrees are not schema-checked.** MAS `$ref`s a sibling spec, PEAS,
  which ships separately from the MAS repo (their own
  `scripts/validate-samples.py` loads `("schemas", "../PEAS/schemas")`).
  Without a PEAS checkout those refs are stubbed permissively and the report
  says exactly which ones went unchecked — "valid" is never allowed to mean
  "fully validated". Pass `--peas <dir>` for complete validation.
- **Core geometry now comes from the OpenMagnetics catalogue**, not a hardcoded
  table. For `TX26/15/10` the catalogue says Ae = 55.0 mm², le = 64.40 mm,
  Ve = 3.542 cm³; the old table said 52.3 mm² / 63.5 mm / 3.321 cm³ — worth
  −4.9 % in B and −6.2 % in Pv. Without `PyOpenMagnetics` installed the
  fallback table is used and labelled as non-authoritative in the provenance.

## Resuming an interrupted run

```
venv\Scripts\python.exe -m opentpt run recipes/tx26_3c90.recipe.json --resume
```

A 40-point sweep that dies at point 35 should not cost 35 points of bench
time, and it does not have to: the journal already records every accepted
point in full, so resuming is a matter of reading it back. Rejects and skips
are **not** restored — a reject is usually a marginal capture, and re-running
one is cheap next to banking a bad point.

The recipe must match. The fingerprint covers everything that changes what a
point *means* — DUT (including resolved geometry), procedures, QC policy,
limits — and a mismatch refuses the resume naming the section that changed,
rather than silently merging two half-datasets. Restored points are marked in
the dataset so a resumed run is never mistaken for one continuous session.

## Bench acceptance

```
venv\Scripts\python.exe -m opentpt accept recipes/tx26_3c90.recipe.json -o acceptance
```

Runs the recipe twice into `run1/` and `run2/` and compares: which points were
accepted, then B, Pv and µ per point against their own tolerances (2 %, 5 %,
5 %). A point accepted in only one of the two runs fails acceptance outright —
that means the rig is sitting on a QC threshold and any single run is a coin
toss.

This measures **repeatability, not accuracy**. A systematic scale error — the
uncalibrated `current_probe_scale` above all — reproduces perfectly and will
pass. The report says so in its own text.

## Tests

```
venv\Scripts\python.exe -m unittest tests.test_engine_analysis \n    tests.test_engine_recipe tests.test_engine_resume tests.test_studio
```

107 tests across the engine, resume/acceptance and Studio suites, no
hardware required. Synthetic cases pin the maths (a sinusoid at a
known loss angle must return that angle; µ″ must reproduce the loop area).
Golden cases pin the pipeline against real captures. One test exists purely to
keep the QC policy honest: `TestKnownBadCapturesAreRejected` asserts that the
two captures from the run that produced a 16535× ratio against MagNet still
fail the gates.
