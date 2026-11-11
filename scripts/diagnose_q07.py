from relay.retrieval import retrieve_passages
from relay.library import build_chunk_library

query = "What is Helium in the Moshi architecture, and what are its model size and pretraining scale?"
print("Query:", query)

for setting in ["setting_1", "setting_2", "setting_3"]:
    res = retrieve_passages(query, setting_name=setting)
    print(f"\nSetting: {setting} (returned {len(res)})")
    for idx, (c, s) in enumerate(res):
        print(f"  Rank {idx+1}: {c.paper} page {c.page} section '{c.section}' score={s:.2f}")

# Check where moshi_v1 page 3 is
chunks = build_chunk_library(target_words=400)
p3_chunks = [c for c in chunks if c.paper == "moshi_v1" and c.page == 3]
print(f"\nTotal p3 chunks in 400-word library: {len(p3_chunks)}")
for c in p3_chunks:
    print(f"P3 chunk id: {c.chunk_id}, text preview: {c.text[:150]}")
