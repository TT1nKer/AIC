# examples

## sample_input.txt → sample_input.distilled.json

Self-authored abstract summary of two brothers / hidden debt / late truth. No copyright issue.

Distilled output extracted:
- 2 characters (responsibility-carrier / silent-burden-bearer)
- 2 relationships (blames ↔ protects_from_truth, double-layered)
- 1 secret (hidden_responsibility_1, A unaware / B full_truth)
- 2 triggers (truth_exposure / public_criticism)

Verifiably **no** original concrete scenes ("grave / account book / dinner / undertaker") appear in output — only structures.

## How to test your own input

```bash
cd story_distiller/src/
python3 distiller.py --in <your-text.txt>
# → writes <your-text>.distilled.json

# With extra anti-leak hints (e.g., known character names to guard against):
python3 distiller.py --in X.txt --leak-hint "张三" --leak-hint "某镇"
```

## Best source material

- Self-written summaries of real stories (no copyright issue)
- Public-domain works (pre-modern Chinese classics, early 20th century works)
- Structurally abstracted retellings (pre-strip names/places/specifics)

**Avoid**: directly feeding in copyrighted novels / scripts / dialogue transcripts.
