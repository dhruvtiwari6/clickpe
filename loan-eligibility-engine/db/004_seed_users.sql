-- Minimal demo batch so B is runnable without the ingestion step.
-- Replace / extend by loading the real sample users.csv via the ingestion UI.
-- Use a FIXED uuid so the demo webhook can target it.
INSERT INTO users (user_id, email, monthly_income, credit_score, employment_status, age, batch_id) VALUES
('u1','abcd+one@gmail.com', 85000, 780, 'salaried',      31, '00000000-0000-0000-0000-000000000001'),
('u2','abcd+two@gmail.com', 28000, 690, 'self_employed', 44, '00000000-0000-0000-0000-000000000001'),
('u3','abcd+one@gmail.com', 15000, 640, 'salaried',      23, '00000000-0000-0000-0000-000000000001'),
('u4','abcd+two@gmail.com', 52000, NULL,'self_employed', 59, '00000000-0000-0000-0000-000000000001')
ON CONFLICT (user_id) DO NOTHING;
-- Note: emails are TEST aliases only; never email the real sample-CSV addresses (see assignment).

