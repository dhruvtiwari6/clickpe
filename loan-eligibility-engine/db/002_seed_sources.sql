INSERT INTO crawl_sources (name, listing_url, requires_js, extraction_mode, selector_config, expected_min_products)
VALUES
('mock-lender-a', 'http://mock-sites/v1.html', false, 'auto',
 '{"card":"div.loan-card",
   "fields":{
     "lender_name":{"css":".lender"},
     "product_name":{"css":"h3.product"},
     "interest_rate":{"css":".roi"},
     "loan_amount":{"css":".amount"},
     "tenure":{"css":".tenure"},
     "min_monthly_income":{"css":".elig .income"},
     "credit_score":{"css":".elig .score"},
     "age":{"css":".elig .age"},
     "employment":{"css":".elig .emp"},
     "processing_fee":{"css":".fee"},
     "detail_url":{"css":"a.apply","attr":"href"}}}', 3),
('mock-lender-b', 'http://mock-sites/v2.html', false, 'auto',
 '{"card":"table.products tbody tr",
   "fields":{
     "lender_name":{"css":"td:nth-child(1)"},
     "product_name":{"css":"td:nth-child(2)"},
     "interest_rate":{"css":"td:nth-child(3)"},
     "min_monthly_income":{"css":"td:nth-child(4)"},
     "credit_score":{"css":"td:nth-child(5)"},
     "employment":{"css":"td:nth-child(6)"}}}', 3)
ON CONFLICT (name) DO NOTHING;
-- Real sources: add ONLY after inspecting the live page + robots.txt.
-- A source with no reliable selectors: extraction_mode='llm', selector_config=NULL.
