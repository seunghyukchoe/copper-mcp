# KiCad root board properties: what a root `(property …)` is, and what it can reach

Research date: 2026-08-12. This note supports
[issue #140](https://github.com/seunghyukchoe/copper-mcp/issues/140), decision
[D-184](../ledgers/decision-ledger.md), risk [R-139](../ledgers/risk-register.md) and
security review [SEC-135](../ledgers/security-ledger.md) and
[ADR-0094](../adr/0094-root-board-properties-as-metadata.md). It is the domain half of that
decision; the measurement half is the before/after corpus count recorded in D-184, taken with
[`scripts/benchmark_real_board_capability.py`](../../scripts/benchmark_real_board_capability.py),
the runner B-099 records. No new benchmark identifier is allocated: this is the same runner and the
same corpus, and the artifact is not committed because it is derived from a private working tree.

Sources are KiCad's own file-format documentation and KiCad's source at `master`
(commit `42cc8baa`, 2026-08-11), cross-read against branch `9.0`. Line numbers are from that
commit and are given so a claim can be re-checked, not because they are stable.

No board content from the surveyed working tree is reproduced here. The construct is described
from its format definition and from KiCad's model of it; the fixtures in
`tests/test_kicad_board_ir.py` are authored from those definitions and carry no board's keys or
values.

## 1 — The measurement

Read-only survey of the same private working tree the
[board-groups note](kicad-board-groups-v1.md) and the
[assembled-outline note](assembled-outline-identity-v1.md) survey, using the same exclusions.
Root `(property …)` occurs on **one project lineage** — the one issue #140 names — at **two
expressions per save**. Four saves in the capability corpus carry it, and on every one of them it
is the refusal the converter *reports*.

**It is not the only construct that refuses them, and issue #140's claim that it is does not
survive measurement.** Conversion stops at the first error, so the reported refusal names the
first blocker in document order and says nothing about what stands behind it. Deleting the two
property expressions from each of those four saves and converting the result on the unmodified
release reproduces a *different* refusal every time — three name a courtyard whose layer disagrees
with its footprint's side (`unsupported.transform`, at `kicad_pcb.footprint[N].courtyard[N]`), and
one names an unsupported field inside a pad (`unsupported.construct`, at
`kicad_pcb.footprint[N].pad[N]`). Accepting the construct therefore converts none of the four; it
advances the refusal by one. That is recorded here because the same inference error — reading one
diagnostic as an exhaustive account — has now been made twice on this corpus, the first time on the
net-tie board D-179 records.

Every occurrence in the tree has exactly the same shape: a single line, one head, two
double-quoted atoms, no children, no third atom. Nothing in the tree carries a root property with
a nested expression, an unquoted atom, or a repeated key.

**No surveyed board references a board-level text variable anywhere.** The counting rule, stated
because the first version of this sentence carried a figure from a narrower file set and a regex
that silently dropped tokens containing digits: every `${…}` token, matched as `\$\{[^}]*\}`, in
exactly the four `.kicad_pcb` saves the capability corpus measures for this lineage — the corpus
excludes `.history/`, `.backup-*` and derived stems, so a wider glob gives a much larger number
that answers a different question. On that set there are exactly two distinct tokens:

| Token | Occurrences | Where | Resolves from |
|---|---:|---|---|
| `${REFERENCE}` | 898 | every one inside an `fp_text` node | the footprint, a built-in — never the properties map |
| `${KICAD10_3DMODEL_DIR}` | 864 | every one inside a `(model …)` path | KiCad's path configuration; a filesystem path, not board text |

**Zero tokens name a board-level key.** So on this corpus
the properties are not merely unmodelled — they are unreferenced. That is a fact about today's
tree and not a property of the construct, and nothing below leans on it.

## 2 — What the format says

The board format enumerates a **Property Section** between Setup and Nets and delegates its
definition to the common section: "See the … properity definition in the s-expression common
section", and "If no properties are defined, this section will not exist"
([board format](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/#_property_section)). The
page states its own scope as "all versions of KiCad from 6.0".

The common section defines it in three sentences
([s-expression common](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html#_properties)):

> "The `property` token defines a key value pair for storing user defined information."
> "The property key attribute is a string that defines the name of the property. Property keys
> must be unique."
> "The property value attribute is a string associated with the key attribute."

The grammar given there is exactly a head, a key string and a value string.

Note for anyone re-reading the docs: the `(property …)` carrying `id`, a position and text effects
is the **symbol/footprint field** variant, which is a different construct at a different level and
is not what a root property is.

## 3 — The writer, exactly

`pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp:735-743`,
`PCB_IO_KICAD_SEXPR::formatProperties()`:

```cpp
for( const std::pair<const wxString, wxString>& prop : aBoard->GetProperties() )
{
    m_out->Print( "(property %s %s)",
                  m_out->Quotew( prop.first ).c_str(),
                  m_out->Quotew( prop.second ).c_str() );
}
```

Three things follow, and the adapter's accepted subset is built from them:

1. **Two atoms, no children.** There is no optional child and no third atom in the writer at all.
2. **Both halves are always quoted.** `Quotew` reaches `OUTPUTFORMATTER::Quotes`
   (`common/richio.cpp:464-500`), which wraps in `"` unconditionally and escapes `\n`, `\r`, `\`
   and `"`.
3. **An empty map emits nothing**, which is why the docs say the section will not exist.

`formatProperties` is called from `formatHeader` (`:782`) after `formatSetup`, matching the
documented section order.

## 4 — The parser, exactly

`pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp:494-506`, `parseBoardProperty()`:

```cpp
NeedSYMBOL();
pName = FromUTF8();
NeedSYMBOL();
pValue = FromUTF8();
NeedRIGHT();
```

dispatched from the board loop at `:1209-1211` (`case T_property: properties.insert(
parseBoardProperty() ); break;`) and stored at `:1407` with `m_board->SetProperties( properties )`.

Consequences that matter to a reader written against it:

- **A third atom or a nested expression is a hard parse error in KiCad too.** `NeedRIGHT()` throws
  unless the next token closes the expression (`common/dsnlexer.cpp:383-389`). A document carrying
  either is not a KiCad board.
- **KiCad's parser accepts an unquoted atom** where its writer never emits one: `NeedSYMBOL`
  admits a bare symbol (`DSNLEXER::IsSymbol`, `common/dsnlexer.cpp:323-329`). This is the one place
  the adapter is deliberately narrower than KiCad.
- **A repeated key is dropped silently.** `std::map::insert` keeps the first value and reports
  nothing, so a document's *expression* count and KiCad's *entry* count can differ. The docs' "keys
  must be unique" is a requirement on the author, not a check.

## 5 — Every read of the board properties map

Storage is `pcbnew/board.h:1792`, `std::map<wxString, wxString> m_properties;`, with plain
`GetProperties()`/`SetProperties()` accessors at `:469-470` and no validation. Whole-repository
survey of the consumers:

| Site | What it does | Reaches geometry, copper, nets, constraints or lock? |
|---|---|---|
| `board.cpp:674-678` `BOARD::ResolveTextVar` | last-resort `${KEY}` lookup | only through substitution — section 6 |
| `board.cpp:4169-4176` `BOARD::operator==` | equality compare | no |
| `pcb_io_kicad_sexpr.cpp:737` | the writer above | no |
| `pcb_edit_frame.cpp:935` | hands the map to `DS_PROXY_VIEW_ITEM` | no — drawing-sheet text |
| `plot_board_layers.cpp:1432,1490`, `pcbnew_jobs_handler.cpp:2858` | `PlotDrawingSheet(…)` | no — drawing-sheet text |
| `pcb_control.cpp:2148,2205-2210` | Append Board; the destination board's keys win | no |

**No call site looks up a board property by name.** The `GetProperties().count("dnp")`,
`"exclude_from_bom"` and `"ki_fp_filters"` hits in `drc_test_provider_schematic_parity.cpp` and
`board_netlist_updater.cpp` are on `COMPONENT::GetProperties()`
(`pcbnew/netlist_reader/pcb_netlist.h:170`), a netlist class, and are easy to mistake for this map.

**There is no reserved board property key.** The format's reserved property keys — `ki_keywords`,
`ki_description`, `ki_locked`, `ki_fp_filters` — are documented under *symbol* properties. A board
key that collides with a built-in text-variable token (`FILENAME`, `PROJECTNAME`, `LAYER`, the
title-block tokens, …) is *shadowed*: those resolve before the map (`board.cpp:632-679`,
`common/project.cpp:95-122`), so the collision makes the property ineffective rather than
powerful.

## 6 — The load-bearing question, and the honest answer

**A root board property is not unconditionally cosmetic, and any decision that assumes it is, is
wrong.** `ResolveTextVar` is a substitution, and **six** of its termini reach something real. The
list below is **an enumeration of what this survey found, not a proof of completeness** — it was
built by following `ResolveTextVar` and `ExpandTextVars` call sites, and a terminus reached by some
path neither name appears on would not be in it. Adding one is expected to be a correction rather
than a surprise.

1. **Text on a copper layer.** `PCB_TEXT::GetShownText` (`pcbnew/pcb_text.cpp:170-196`) resolves
   through `board->ResolveTextVar`. Glyphs on a copper layer are plotted copper and are
   DRC-checked, so a property value can change copper geometry.
2. **Text boxes.** `PCB_TEXTBOX::GetShownText` resolves through the same call, so a
   `gr_text_box` on copper carries the same consequence as a `gr_text`.
3. **Table cells.** `PCB_TABLECELL::GetShownText` — the root `(table …)` construct KiCad 9 and 10
   write — resolves the same way, one more container whose rendered glyphs vary with the value.
4. **Barcodes.** `PCB_BARCODE::AssembleBarcode` builds the Zint symbol from `GetShownText()`
   (`pcbnew/pcb_barcode.cpp:155,556`), so the module pattern itself varies with the value.
5. **Custom DRC rules.** `DRC_ENGINE::loadRules` (`pcbnew/drc/drc_engine.cpp:664-694`) runs every
   line of the `.kicad_dru` through `ExpandTextVars` with `m_board->ResolveTextVar` *before*
   `DRC_RULES_PARSER::Parse`. A rule reading `(constraint clearance (min ${MIN_CLR}))` therefore
   takes its clearance from a board property. This is the strongest counterexample to the cosmetic
   reading and the reason this note exists.
6. **The IPC API.** `api_handler_board.cpp:909` exposes an `ExpandTextVariables` endpoint, so an
   IPC client can read an expanded value directly rather than through any board object. It changes
   no geometry; it is listed because it makes the map *reachable* by something other than a
   renderer, which matters to anyone reasoning about disclosure.

Also side-effectful but outside any board model: plot, drill, position and STEP **output
directories** are expanded through `ResolveTextVar` (`dialog_plot.cpp:820,1251,1369`,
`dialog_gendrill.cpp:318-327`, `dialog_gen_footprint_position.cpp:297,414`,
`dialog_export_step.cpp:545`), so a property can redirect where a file is written.

What a root property never does directly: set a netclass, a stackup, a layer, a board thickness, a
lock, or any design-setting field. Every read treats the value as an opaque string.

### Why the accept is nonetheless sound for this adapter

The argument is *not* that the construct is inert. It is that **every terminus above is already
refused by, or already outside, this adapter — for its own reasons, and independently of whether
any property is present**:

| Terminus | This adapter's existing behaviour | Pinned by a property-coupled test? |
|---|---|---|
| Root text on a copper layer (`gr_text`, `gr_text_box`) | refused by the `gr_*` branch on any copper layer (issue #141 owns the sentence) | yes |
| Footprint text on a copper layer (`fp_text`, footprint field) | refused: a footprint graphic on a copper layer is unmodelled copper | yes |
| `(barcode …)` | not in the root vocabulary; refused without being named | yes |
| `(table …)` | not in the root vocabulary; refused without being named | no — same mechanism as `barcode`, not separately pinned |
| `.kicad_dru` custom rules | never parsed by this adapter — stated in ADR-0005 and in [the Board IR contract](../architecture/board-ir.md) | **no, and by design** |
| IPC `ExpandTextVariables` | a KiCad-side endpoint; CopperMCP's observer never calls it | not applicable |

Two rows deserve their own sentence rather than a tick. **The `.kicad_dru` leg is pinned by
nothing**, because it is the absence of a parsing path: a test asserting that a file is never
opened pins an implementation detail, not the property, and would pass just as happily if the rules
were read somewhere else. It rests on the architecture statement and on review. The `(table …)` row
is unpinned for a weaker reason — it refuses through exactly the mechanism `barcode` does, an
unknown root head, so the `barcode` pin covers the mechanism if not the spelling.

and because Board IR **carries no text of any kind**. A `Footprint` is an id, an origin, a
rotation, a side, pad ids and courtyards; no reference designator, value string, field or title
block is modelled, and the only document string that reaches a snapshot is a layer name, which
KiCad does not expand text variables into.

The custom-rule case deserves its own sentence, because "we do not read it" would be a poor answer
on its own. CopperMCP's authoritative DRC surface does not re-implement rules: `kicad_cli.py`
carries `.kicad_pro` and `.kicad_dru` into the DRC context and runs **KiCad** over the board bytes,
and the write-back path preserves a root property byte-for-byte (pinned by
`test_a_placement_splice_leaves_a_root_board_property_byte_identical`). So the one surface that
claims to honour a custom rule still honours it, with the property expanded by KiCad itself from
the real value.

## 7 — The round-trip hazard, recorded rather than fixed

`BOARD::SynchronizeProperties()` (`board.cpp:2967-2971`) is

```cpp
if( m_project && !m_project->IsNullProject() )
    SetProperties( m_project->GetTextVars() );
```

— it **replaces the whole map** from the project's `text_variables`, and it is called on save
(`pcbnew/files.cpp:1038`), on `TEXTVARS_CHANGED` (`pcb_base_frame.cpp:946`) and from the jobs
handler. The Board Setup → Text Variables panel edits `PROJECT::GetTextVars()`, i.e. the
`.kicad_pro`, not the board. So root board properties are best understood as a **cache of the
project's text variables**: a board-only key does not survive a GUI save with a real project
loaded. Nothing CopperMCP does depends on that, and nothing here proposes to write the map — it is
recorded so that a future round-trip feature does not rediscover it the hard way.

Precedence between the two sources changed after 9.0 and is worth knowing for the same reason:
`master` resolves title block → project text vars → board properties (`board.cpp:662-679`, pinned
by `qa/tests/pcbnew/test_text_variable_resolution.cpp`), while `9.0` resolves board properties
first (`board.cpp:545-557` on that branch). CopperMCP reads neither, so neither ordering reaches
any claim it makes.

## 8 — What this note does not establish

- It does not claim the construct is inert in KiCad. Section 6 says the opposite.
- It does not verify a released KiCad 10.0 binary; the source read is `master` at 10.0-dev, and
  the syntax is identical on `9.0`.
- It says nothing about boards written by tools other than KiCad, whose `(property …)` may not
  follow the writer shape in section 3. The adapter's closed field table is the defence: a
  document outside that shape refuses.
- It makes no claim about a board's *converted* content being correct in any respect other than
  that a root property does not change it.
