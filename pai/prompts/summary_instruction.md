You are creating a summary node for the assistant's hierarchical memory. This memory is not indexed by embedding search — the summary node is the ONLY way to understand what's inside before someone opens a fragment via get_fragment. So write a navigation table of contents, not a retelling.

The format is mandatory:
1) An overall summary of the group's topic (1-2 sentences).
2) A line-by-line list FOR EACH fragment in the form:
   - (id=<exact_id>) <precise title/topic of the fragment, 3-8 words> — <exactly what's in it: facts, decisions, entities, dates, proper names>
Each list line must contain the fragment's exact id and be sufficient to decide whether it's worth opening that id via get_fragment, WITHOUT opening it. Use concrete names, terms, and entities from the fragment, not generic words like 'discussion' or 'information'. Don't invent facts that aren't in the fragments.
