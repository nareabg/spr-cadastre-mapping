# SPR addresses → cadastre id

This project maps each row of the SPR address file (`spr_all_unmapped_addresses.csv`) to one cadastre address id. When a row can't be mapped, it gets a precise issue category and the team that should look at it: cadastre, SPR or a reviewer.

**Rule:** a row gets a `cadastre_id` only when exactly one cadastre address fits and the place, street, house and apartment all agree. Anything weaker becomes a suggestion or an issue, never a mapping.

## Results (614,083 SPR rows, 12 Oct 2026 run)

| status | rows | % |
|---|---:|---:|
| mapped · high confidence | 36,561 | 5.95 |
| mapped · medium confidence | 70,244 | 11.44 |
| suggested (needs a yes from a reviewer) | 32,517 | 5.30 |
| ambiguous (several cadastre ids fit) | 28,404 | 4.63 |
| not_found | 348,377 | 56.73 |
| out_of_scope (foreign or blank region) | 97,980 | 15.96 |

The street was found for 391,235 of the 516,103 rows inside Armenia. Most of those then stop because the house (147,974 rows) or the apartment (82,611 rows) number is not in the cadastre file. **The cadastre file has exactly 1,048,575 rows, which is Excel's limit, so the export was very likely cut off.** A re-run on a full export should map more rows.

## Run

Both ways run the same code and give the same results:

```
pip install -r requirements.txt
# put the two input files in data/input/
jupyter notebook walkthrough.ipynb   # step by step: run a step, look at its tables, go on
python run_all.py                    # everything at once (about 8 minutes)
```

`walkthrough.ipynb` runs each step and then shows what it produced (counts, examples, the rows behind each rule), so you can stop and check at any point. It is saved with the outputs of the 12 Oct run, so you can read it without running it.

Investigation helpers (`mapping/explore.py`, already loaded in the notebook):

| function | shows |
|---|---|
| `explain(spr_id)` | every decision the pipeline made for one SPR row |
| `search_cadastre('բաղրամյան', 'Արաբկիր')` | cadastre streets whose name contains a text |
| `streets_in('Արաբկիր')` | all cadastre streets of a settlement or Yerevan district |
| `houses_on(street_uid)` | all cadastre addresses on one street |
| `sample('CAD_HOUSE_NOT_FOUND', 20)` | random rows of one issue category |
| `q("SELECT ...")` | any SQL on the working tables |

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
data/input/               the two input files (not included)
output/                   results
work/mapping.duckdb       intermediate tables, rebuilt by every run
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

- **The cadastre export is probably truncated** (exactly 1,048,575 rows). Re-run with a full CSV or parquet export before treating `CAD_HOUSE_NOT_FOUND` and `CAD_APARTMENT_NOT_FOUND` as real cadastre gaps.
- **90,073 cadastre ids duplicate another id's address and object type.** SPR rows that point at them stay ambiguous until the cadastre team resolves them.
