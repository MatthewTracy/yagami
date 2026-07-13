-- v0.3: config profiles. Records which named profile (if any) was active
-- when a routing decision was made, so the Privacy Ledger / audit export
-- can show which policy governed a given turn. NULL = no profile active.
ALTER TABLE decisions ADD COLUMN profile TEXT;
