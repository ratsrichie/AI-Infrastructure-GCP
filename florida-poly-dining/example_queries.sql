USE flpoly_dining;
-- Count source listings, including legitimate repeated category listings.
SELECT r.name, COUNT(m.id) AS menu_listings FROM restaurants r
LEFT JOIN menu_items m ON m.restaurant_id=r.id GROUP BY r.id,r.name ORDER BY r.id;
-- Find recorded vegan-tagged options. Unknown tags are not inferred.
SELECT r.name AS restaurant, m.name, m.portion_text, m.calories_text, m.source_page
FROM menu_items m JOIN restaurants r ON r.id=m.restaurant_id
WHERE FIND_IN_SET('VG', REPLACE(m.dietary_tags,' ','')) > 0;
-- Buffet admission is separate from item prices.
SELECT name, buffet_admission_usd, price_provenance FROM restaurants;
-- Source errors remain visible rather than being silently corrected.
SELECT name,portion_text,calories_text,description FROM menu_items WHERE likely_source_error=1;
-- Retrieve the preserved conflicting hours and their week.
SELECT r.name,c.context_text FROM restaurants r JOIN restaurant_context c ON c.restaurant_id=r.id;
-- Native vectors are populated only after scripts/embed.py runs with local Ollama.
SELECT (SELECT COUNT(*) FROM rag_chunks) AS chunks,
       (SELECT COUNT(*) FROM rag_embeddings) AS embedded_chunks;
