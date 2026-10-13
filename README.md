# SPR addresses → cadastre id

This project maps each row of the SPR address file (`spr_all_unmapped_addresses.csv`) to one cadastre address id. When a row can't be mapped, it gets a precise issue category and the team that should look at it: cadastre, SPR or a reviewer.

**Rule:** a row gets a `cadastre_id` only when exactly one cadastre address fits and the place, street, house and apartment all agree. Anything weaker becomes a suggestion or an issue, never a mapping.

## Results (614,083 SPR rows, 13 Oct 2026 run, full cadastre in 2 parts)

| status | rows | % |
|---|---:|---:|
| mapped · high confidence | 24,015 | 3.91 |
| mapped · medium confidence | 80,131 | 13.05 |
| suggested (needs a yes from a reviewer) | 30,425 | 4.95 |
| ambiguous (several cadastre ids fit) | 55,119 | 8.98 |
| not_found | 326,413 | 53.15 |
| out_of_scope (foreign or blank region) | 97,980 | 15.96 |

The cadastre is now read from `cadastre_all_data_part1.xlsx` + `cadastre_all_data_part2.xlsx` (1,391,811 distinct ids). The street was found for 393,207 of the 516,103 rows inside Armenia; most of the rest stop at the house (135,900 rows) or the apartment (75,541 rows), because that number is not in the cadastre.

**Compared with the first run, which used a cadastre file cut off at Excel's row limit:** 19,181 rows gained a cadastre id (mostly because their house or apartment was in the missing part). 21,839 rows that had been mapped became ambiguous: the full cadastre holds a second id for the same address and object, which the cut-off file had hidden. Those rows are now listed under `CAD_DUPLICATE_ADDRESS` (48,714 rows in total) for the cadastre team.

## Run

Both ways run the same code and give the same results:

```
pip install -r requirements.txt
# put the input files in data/input/: spr_all_unmapped_addresses.csv and the
# cadastre file(s) - every file whose name starts with cadastre_all_data is read
jupyter notebook walkthrough.ipynb   # step by step: run a step, look at its tables, go on
python run_all.py                    # everything at once (about 8 minutes)
```

`walkthrough.ipynb` runs each step and then shows what it produced (counts, examples, the rows behind each rule), so you can stop and check at any point. It is saved with the outputs of the 13 Oct run, so you can read it without running it.

Investigation helpers (`mapping/explore.py`, already loaded in the notebook):

| function | shows |
|---|---|
| `explain(spr_id)` | every decision the pipeline made for one SPR row |
| `search_cadastre('բաղրամյան', 'Արաբկիր')` | cadastre streets whose name contains a text |
| `streets_in('Արաբկիր')` | all cadastre streets of a settlement or Yerevan district |
| `houses_on(street_uid)` | all cadastre addresses on one street |
| `sample('CAD_HOUSE_NOT_FOUND', 20)` | random rows of one issue category |
| `q("SELECT ...")` | any SQL on the working tables |

## Dashboard (for non-coders)

After a pipeline run, open the dashboard:

```
streamlit run dashboard/app.py      # or double-click run_dashboard.bat (Windows) / run_dashboard.command (Mac)
```

It opens in the browser at http://localhost:8501 and runs only on your computer. The first start takes about a minute: `dashboard/prepare_data.py` reads `output/` and the cadastre file(s) in `data/input/` and writes `dashboard/data/*.parquet`. It does this again by itself whenever the pipeline outputs are newer.

`dashboard/data/` is committed to git, so someone who clones the repo can open the dashboard without running the pipeline; it then shows the results of the last run that was pushed. After a new pipeline run, push `dashboard/data/` again so the two stay in step.

| tab | what you can do |
|---|---|
| 📊 Overview | headline counts, status split, issue categories by owner, results by region, which rule mapped how many rows, earlier mapping vs this one |
| 🏠 Cadastre data | every cadastre id with filters (region, community, settlement, street, house, type), its data issues and how many SPR rows point at it |
| ⚠️ SPR issues | every SPR row that is not mapped, by owner and issue category, with the streets that cause the most issues |
| ✅ SPR mapping result | the final result file with all columns, filters on everything, and a one-row view of an SPR address next to its cadastre address |
| 🧾 Cadastre issues | the cadastre-only checks (duplicates, spelling variants, missing names …) with lookup by id |
| 📖 Glossary | what every status, issue category and cadastre check means |

Every table can be downloaded as CSV with the filters applied. The dashboard only reads the outputs; it never changes the pipeline or its files. It shows the SPR file's earlier `match_type` for comparison only.

## Folder map

```
README.md                 this page
walkthrough.ipynb         step-by-step run with intermediate results
run_all.py                runs steps 1-7 in one go
config.py                 input / output paths
mapping/                  shared logic
  aliases.py              all aliases, copied from the earlier projects (none new)
  normalize.py            text, place and number normalisation
  street_parser.py        street text -> structured street, alternative readings
  street_matcher.py       the 7 street rules
  explore.py              investigation helpers
  db.py                   DuckDB helpers
steps/
  s01_load.py             load both files untouched, add spr_id
  s02_cadastre.py         cadastre keys and cadastre data-quality checks
  s03_prepare_spr.py      put SPR geography on the cadastre's hierarchy
  s04_match_streets.py    find the cadastre street
  s05_match_addresses.py  house, apartment, object type -> cadastre id
  s06_export.py           final status, issue categories, output files
  s07_checks.py           integrity checks (the run fails if one fails)
data/input/               the input files: SPR csv + cadastre part(s)
output/                   results
work/mapping.duckdb       intermediate tables, rebuilt by every run
dashboard/                the dashboard (app.py) and its data builder (prepare_data.py)
run_dashboard.bat         starts the dashboard on Windows
run_dashboard.command     starts the dashboard on Mac
```

## How a row is matched

1. **Geography** (`s03`). The colleagues' corrections run in their order: `geo_alias`, then `community_alias`, then the Yerevan district shift, then the Gyumri settlement fill. The search area is the settlement or Yerevan district the row names.
2. **Street** (`s04`). The search runs area by area, rule by rule and reading by reading, and the first hit decides.

   | # | rule | allows | confidence |
   |---|---|---|---|
   | 1 | exact | identical cleaned name | high |
   | 2 | alias | colleagues' renames (Գյումրի, Կապան) | high |
   | 3 | double_letter | notebook typo list, doubled letters | high |
   | 4 | word_form | genitive endings (`ԳԱՌՆԻԻ` = `Գառնու`) | high |
   | 5 | extra_words | SPR words are a subset of the cadastre words (`ԱՆԴՐԱՆԻԿԻ` = `Զորավար Անդրանիկի`); generic words like `ՆՈՐ` or `ԹԱՂԱՄԱՍ` alone don't count | medium |
   | 6 | initial_for_name | `Ք.` = `ՔԸՐՔ`, or one side omits the initial; two different initials never match | medium |
   | 7 | fuzzy | letters only, score ≥ 90 and 4 points clear of the next street; never used for a bare word with no type | medium |

   Numbers are never fuzzy. Street numbers, block codes (`Ա-1`, `110ա`) and lane numbers must be identical and in the same order. If several streets fit, the row is ambiguous. If the name is found but the type differs (`Փ.` vs `պողոտա`), the row is not mapped.

   The text is read in several ways: `as_written`, `neighbourhood_street` (`Ա2 ԹՂՄ. 9 Փ.`), `lane_of_street` (`ԴՐՈՅԻ 4 ՆՐԲ.`), `without_place_prefix` and `without_house_numbers`.
3. **Address** (`s05`). The house and apartment numbers must be identical after cleaning. If the SPR row has no apartment, the cadastre row must have no building number either. If several ids remain, the SPR house type (`Տ`, `Շ`, `ՀՈՂ`) picks the matching object. A single id that is a different kind of object, such as a garage or a land plot, becomes a suggestion.
4. **Final** (`s06`). An address found outside the place the SPR row names becomes a suggestion: another Yerevan district, or a city-wide search because no district is given. `high` confidence needs rules 1-4, read as written or as neighbourhood + street, one cadastre id, and an object type that agrees. Every other mapped row is `medium`.

## Outputs (`output/`)

| file | for whom | what |
|---|---|---|
| `spr_mapping_result.csv` / `.parquet` | everyone | one row per SPR row: `spr_id`, original columns, `cadastre_id`, `match_status`, `match_confidence`, `issue_category`, `issue_owner`, `issue_detail`, suggestion, method, `data_completeness`, and the matched cadastre row next to it |
| `00_summary.xlsx` | management | counts by status, category and method; cadastre data checks |
| `issues_for_cadastre.xlsx` | cadastre team | missing houses and apartments, duplicate ids, plus checks on the cadastre file alone |
| `issues_for_spr.xlsx` | SPR team | foreign addresses, missing street, house or apartment, unreadable street text, SPR rows that are the same address |
| `needs_review.xlsx` | reviewer | suggestions and ambiguous rows that need a decision |
| `spr_with_id.csv` | everyone | the SPR file as received, with `spr_id` as the first column |

`spr_id` is an identity key, 1…614,083 in the file's row order. Use it to find any row in any output. The SPR file's `match_type` and `cad_candidate_ids` columns come from an earlier mapping and are never read.

## Where the logic comes from

| logic | source |
|---|---|
| Normaliser (ligature `և`, ordinals, filler words, `Գ.`/`Ք.` prefix, `(…)` qualifiers, genitive endings, house-number cleaning, literal folds), street renames for Գյումրի and Կապան, the `Խ. ՀԱՅՐԻԿ` alias | `address-matcher/models/address_models.py`, `01_hasce_address_staged/00_normalize_functions.sql` |
| Armenian-region gate, `geo_alias`, `community_alias`, Yerevan and Gyumri rules, `data_completeness` | `01_hasce_address_staged/01_hasce_address_staged.sql` |
| One match = mapped, several = ambiguous | `03_address_match.sql` |
| Unmatched reasons by level (community, settlement, street, house) | `04_unmatched_reasons.sql`, `unmatched_categories.sql`, `qa/cadastre_match_qa.sql` |
| `nrm`/`gnrm` fold, type abbreviations, house-word cut, typo list, subset and initial guards, numbers-must-match fuzzy guard | `Project1_corrected (4).ipynb` |
| Rule order, `text_reading`, `searched_in`, `type_check`, high vs medium | `Match_Result_Fields.html` |

There are two lookups of abbreviations, not of names. `street_parser.TYPE_WORDS` maps `ՄԿՐՇ.`, `ԶՆԳՎ.`, `Մ/Շ` and others to type words, as the notebook's `TYPE_MAP` does. `s05.HOUSE_TYPE_CODES` maps SPR house codes such as `Տ` = `բնակելի տուն`.

**Colleague logic not used, and why**

- **Admin-level fallback** (`03_address_match.sql`, step 1b). It gives rows with no street, house or apartment a synthetic id built from region, community and settlement codes. In this SPR file all 20,842 such rows are foreign, and the codes need `cadastre_imp` reference tables that the cadastre file doesn't include.
- **Person re-join and source profiling** (`05_person_address.sql`, `qa/address_source_profile.sql`, `qa/psns_without_address.sql`, `qa/hasce_profile.sql`). These work on person numbers and the Oracle source tables, which this file doesn't have. `spr_id` is the key for joining this result back to persons later.

## Known limits

- **225,538 cadastre ids share an address and object type with another id** (`CAD_DUPLICATE_ADDRESS`, sheet `cadastre_only_checks`). SPR rows pointing at them (48,714) stay ambiguous until the cadastre team keeps one id per address.
- **Five rows appear twice in the cadastre parts** (same id, same values). They are kept once at load time; the step-1 log reports it.
