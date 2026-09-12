SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS users (
                    tg_id       BIGINT PRIMARY KEY,
                    username    TEXT,
                    balance     BIGINT NOT NULL DEFAULT 0,  -- USDC micro-units (1e6)
                    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
                    -- P2: Smart Wallet (ERC-4337)
                    smart_address  TEXT,      -- deterministic SmartAccount address
                    smart_deployed BOOLEAN DEFAULT false,  -- deployed on-chain?
                    smart_created_at BIGINT   -- epoch when smart wallet was created
                );
                CREATE INDEX IF NOT EXISTS idx_users_username ON users (username);
                CREATE TABLE IF NOT EXISTS tx_log (
                    id        BIGSERIAL PRIMARY KEY,
                    kind      TEXT NOT NULL,             -- deposit | tip | withdraw
                    tg_id     BIGINT NOT NULL,
                    counterparty TEXT,
                    amount    BIGINT NOT NULL,           -- micro-units
                    tx_hash   TEXT,
                    note      TEXT,
                    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE INDEX IF NOT EXISTS idx_tx_log_tg ON tx_log (tg_id);
                CREATE INDEX IF NOT EXISTS idx_tx_log_kind ON tx_log (kind);
                -- Composite indexes for the reporting/aggregate queries that
                -- group/filter on (kind, time) / (kind, counterparty): without
                -- them volume_history / user_stats / top_tippers scan the
                -- append-only tx_log table per request.
                CREATE INDEX IF NOT EXISTS idx_tx_log_kind_ct ON tx_log (kind, created_at);
                CREATE INDEX IF NOT EXISTS idx_tx_log_kind_cp ON tx_log (kind, counterparty);
                CREATE TABLE IF NOT EXISTS pending_deposits (
                    tx_hash      TEXT PRIMARY KEY,
                    sender       TEXT NOT NULL,
                    amount_micro BIGINT NOT NULL,
                    block        BIGINT,          -- deposit block; NULL = legacy row
                    claimed      BIGINT NOT NULL DEFAULT 0
                );
                ALTER TABLE pending_deposits ADD COLUMN IF NOT EXISTS block BIGINT;
                ALTER TABLE pending_deposits ADD COLUMN IF NOT EXISTS created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint);
                CREATE INDEX IF NOT EXISTS idx_pending_deposits_sender ON pending_deposits (LOWER(sender));
                CREATE INDEX IF NOT EXISTS idx_pending_deposits_claimed ON pending_deposits (claimed);
                CREATE TABLE IF NOT EXISTS link_nonces (
                    tg_id       BIGINT PRIMARY KEY,
                    address     TEXT NOT NULL,
                    nonce       TEXT NOT NULL,
                    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE TABLE IF NOT EXISTS wallet_links (
                    tg_id     BIGINT PRIMARY KEY,
                    address   TEXT NOT NULL UNIQUE,
                    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE TABLE IF NOT EXISTS bets (
                    id          BIGSERIAL PRIMARY KEY,
                    creator     BIGINT NOT NULL,
                    question    TEXT NOT NULL,
                    options     TEXT NOT NULL,                -- JSON array
                    status      TEXT NOT NULL DEFAULT 'open', -- open | resolved | cancelled
                    winner      BIGINT,
                    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE INDEX IF NOT EXISTS idx_bets_status ON bets (status);
                CREATE TABLE IF NOT EXISTS bet_positions (
                    id           BIGSERIAL PRIMARY KEY,
                    bet_id       BIGINT NOT NULL,
                    tg_id        BIGINT NOT NULL,
                    option_idx   BIGINT NOT NULL,
                    amount_micro BIGINT NOT NULL,
                    created_at   BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE INDEX IF NOT EXISTS idx_bet_positions_bet ON bet_positions (bet_id);
                CREATE TABLE IF NOT EXISTS last_block (
                    id    BIGINT PRIMARY KEY CHECK (id = 1),
                    block BIGINT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS message_authors (
                    chat_id    BIGINT NOT NULL,
                    message_id BIGINT NOT NULL,
                    tg_id      BIGINT NOT NULL,
                    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
                    PRIMARY KEY (chat_id, message_id)
                );
                CREATE TABLE IF NOT EXISTS reaction_tips (
                    chat_id    BIGINT NOT NULL,
                    message_id BIGINT NOT NULL,
                    tg_id      BIGINT NOT NULL,
                    amount_micro BIGINT NOT NULL,
                    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
                    PRIMARY KEY (chat_id, message_id, tg_id)
                );
                CREATE INDEX IF NOT EXISTS idx_reaction_tips_tg ON reaction_tips (tg_id);
                CREATE TABLE IF NOT EXISTS user_settings (
                    tg_id         BIGINT PRIMARY KEY,
                    reaction_tips BIGINT NOT NULL DEFAULT 1,  -- allow emoji-reaction tips
                    notify_deposits BIGINT NOT NULL DEFAULT 1, -- DM on credited deposit
                    lang          TEXT NOT NULL DEFAULT 'ru'   -- UI language: ru/en/zh
                );
                CREATE TABLE IF NOT EXISTS user_wallets (
                    id         BIGSERIAL PRIMARY KEY,
                    tg_id      BIGINT NOT NULL,
                    address    TEXT NOT NULL UNIQUE,
                    key_enc    TEXT NOT NULL,
                    seed_enc   TEXT NOT NULL,
                    slot       INT NOT NULL DEFAULT 1,
                    active     BOOLEAN NOT NULL DEFAULT true,
                    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
                    UNIQUE (tg_id, slot)
                );
                CREATE TABLE IF NOT EXISTS x402_payments (
                    tx_hash      TEXT PRIMARY KEY,
                    recipient_tg BIGINT NOT NULL,
                    amount_micro BIGINT NOT NULL,
                    sender       TEXT NOT NULL,
                    pay_to       TEXT,
                    created_at   BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                -- Per-invoice x402 receive addresses. Each invoice mints a
                -- unique derived address bound to (recipient, amount, kind and
                -- optional ref), so a payment can never be redeemed against a
                -- different invoice (closes the legacy tx-hash frontrun).
                CREATE TABLE IF NOT EXISTS x402_invoices (
                    invoice_id   TEXT PRIMARY KEY,
                    pay_addr     TEXT NOT NULL UNIQUE,
                    recipient_tg BIGINT NOT NULL,
                    amount_micro BIGINT NOT NULL,
                    kind         TEXT NOT NULL,             -- 'tip' | 'paywall'
                    ref_id       TEXT,
                    pay_to       TEXT,                      -- consolidated receive address (sweep target)
                    created_at   BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
                    credited     BOOLEAN NOT NULL DEFAULT false,
                    swept_at     BIGINT
                );
                CREATE INDEX IF NOT EXISTS idx_x402_invoices_recv ON x402_invoices (recipient_tg);
                CREATE INDEX IF NOT EXISTS idx_x402_invoices_credited ON x402_invoices (credited);
                CREATE TABLE IF NOT EXISTS paywall_items (
                    id          BIGSERIAL PRIMARY KEY,
                    owner_tg    BIGINT NOT NULL,
                    title       TEXT NOT NULL,
                    price_micro BIGINT NOT NULL,
                    content     TEXT NOT NULL,
                    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE TABLE IF NOT EXISTS paywall_purchases (
                    id          BIGSERIAL PRIMARY KEY,
                    item_id     BIGINT NOT NULL,
                    buyer_tg    BIGINT,
                    tx_hash     TEXT,
                    amount_micro BIGINT NOT NULL,
                    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
                    UNIQUE (item_id, buyer_tg),
                    UNIQUE (item_id, tx_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_paywall_purchases_item ON paywall_purchases (item_id);
                CREATE TABLE IF NOT EXISTS paywall_channels (
                    chat_id     BIGINT PRIMARY KEY,
                    owner_tg    BIGINT NOT NULL,
                    price_micro BIGINT NOT NULL,
                    period_days BIGINT NOT NULL DEFAULT 30,
                    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE TABLE IF NOT EXISTS paywall_subscriptions (
                    chat_id    BIGINT NOT NULL,
                    tg_id      BIGINT NOT NULL,
                    expires_at BIGINT NOT NULL,
                    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
                    PRIMARY KEY (chat_id, tg_id)
                );
                CREATE INDEX IF NOT EXISTS idx_paywall_subscriptions_expires
                    ON paywall_subscriptions (expires_at);
                CREATE TABLE IF NOT EXISTS markets (
                    id            BIGSERIAL PRIMARY KEY,
                    creator       BIGINT NOT NULL,
                    question      TEXT NOT NULL,
                    options       TEXT NOT NULL,            -- JSON array
                    status        TEXT NOT NULL DEFAULT 'open', -- open | resolved | cancelled
                    winner        BIGINT,
                    close_at      BIGINT,
                    subsidy_micro BIGINT NOT NULL,          -- creator deposit (AMM funding)
                    b_micro       BIGINT NOT NULL,          -- LMSR liquidity param (micro-USDC)
                    escrow_micro  BIGINT NOT NULL DEFAULT 0,-- AMM cash held (subsidy + buys - sells)
                    deadline_notified BIGINT NOT NULL DEFAULT 0,
                    grace_warned  BIGINT NOT NULL DEFAULT 0,
                    created_at    BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
                );
                CREATE INDEX IF NOT EXISTS idx_markets_status ON markets (status);
                CREATE TABLE IF NOT EXISTS market_shares (
                    market_id  BIGINT NOT NULL,
                    tg_id      BIGINT NOT NULL,
                    option_idx BIGINT NOT NULL,
                    shares     BIGINT NOT NULL DEFAULT 0,     -- micro-shares (1e6 shares = 1 USDC payout)
                    cost_micro BIGINT NOT NULL DEFAULT 0,     -- net paid; negative = realized profit
                    PRIMARY KEY (market_id, tg_id, option_idx)
                );
                CREATE INDEX IF NOT EXISTS idx_market_shares_user ON market_shares (tg_id);
ALTER TABLE bets ADD COLUMN IF NOT EXISTS close_at BIGINT;
ALTER TABLE tx_log ADD COLUMN IF NOT EXISTS status TEXT;
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS lang TEXT NOT NULL DEFAULT 'ru';
ALTER TABLE bets ADD COLUMN IF NOT EXISTS deadline_notified BIGINT NOT NULL DEFAULT 0;
ALTER TABLE bets ADD COLUMN IF NOT EXISTS grace_warned BIGINT NOT NULL DEFAULT 0;
ALTER TABLE x402_payments ADD COLUMN IF NOT EXISTS pay_to TEXT;
CREATE TABLE IF NOT EXISTS suspicious_activity (
    id          BIGSERIAL PRIMARY KEY,
    tg_id       BIGINT NOT NULL,
    kind        TEXT NOT NULL,        -- large_withdraw | rapid_withdraw | unusual_deposit
    details     TEXT NOT NULL,         -- JSON: amount, threshold, count, etc.
    severity    TEXT NOT NULL DEFAULT 'info',  -- info | warn | critical
    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
);
CREATE INDEX IF NOT EXISTS idx_suspicious_tg ON suspicious_activity (tg_id);
CREATE INDEX IF NOT EXISTS idx_suspicious_created ON suspicious_activity (created_at);
CREATE TABLE IF NOT EXISTS community_treasuries (
    id          BIGSERIAL PRIMARY KEY,
    chat_id     BIGINT NOT NULL UNIQUE,
    owner_tg    BIGINT NOT NULL,
    balance     BIGINT NOT NULL DEFAULT 0,
    quorum_pct  INTEGER NOT NULL DEFAULT 50,
    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
);
CREATE TABLE IF NOT EXISTS treasury_transactions (
    id          BIGSERIAL PRIMARY KEY,
    treasury_id BIGINT NOT NULL REFERENCES community_treasuries(id),
    kind        TEXT NOT NULL,
    tg_id       BIGINT,
    amount      BIGINT NOT NULL,
    note        TEXT,
    tx_hash     TEXT,
    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
);
CREATE TABLE IF NOT EXISTS treasury_proposals (
    id          BIGSERIAL PRIMARY KEY,
    treasury_id BIGINT NOT NULL REFERENCES community_treasuries(id),
    proposer_tg BIGINT NOT NULL,
    amount      BIGINT NOT NULL,
    to_address  TEXT NOT NULL,
    description TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'voting',
    votes_yes   INTEGER NOT NULL DEFAULT 0,
    votes_no    INTEGER NOT NULL DEFAULT 0,
    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
    closes_at   BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS treasury_votes (
    id          BIGSERIAL PRIMARY KEY,
    treasury_id BIGINT NOT NULL REFERENCES community_treasuries(id),
    proposal_id BIGINT NOT NULL,
    tg_id       BIGINT NOT NULL,
    vote        INTEGER NOT NULL,
    created_at  BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
    UNIQUE (proposal_id, tg_id)
);
CREATE TABLE IF NOT EXISTS gas_drips (
    day  BIGINT PRIMARY KEY,              -- UTC day (unix // 86400)
    count BIGINT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS market_subsidies (
    day  BIGINT PRIMARY KEY,              -- UTC day (unix // 86400)
    total_micro BIGINT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS onchain_markets (
    id         BIGINT PRIMARY KEY,               -- on-chain OutcomeMarket marketId
    creator    BIGINT NOT NULL,
    question   TEXT NOT NULL,
    options    TEXT NOT NULL,                    -- JSON array (labels live off-chain)
    close_at   BIGINT NOT NULL,
    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
);
CREATE INDEX IF NOT EXISTS idx_onchain_markets_close ON onchain_markets (close_at);
ALTER TABLE onchain_markets ADD COLUMN IF NOT EXISTS deadline_notified BIGINT NOT NULL DEFAULT 0;
ALTER TABLE onchain_markets ADD COLUMN IF NOT EXISTS resolved_outcome BIGINT;
ALTER TABLE onchain_markets ADD COLUMN IF NOT EXISTS cancelled_flag BIGINT NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS onchain_trades (
    id         BIGSERIAL PRIMARY KEY,
    market_id  BIGINT NOT NULL,
    tg_id      BIGINT NOT NULL,
    outcome    BIGINT NOT NULL,
    shares     BIGINT NOT NULL,              -- micro-shares bought (at buy time)
    tx_hash    TEXT,
    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
);
CREATE INDEX IF NOT EXISTS idx_onchain_trades_market ON onchain_trades (market_id, outcome);
CREATE TABLE IF NOT EXISTS notification_outbox (
    id         BIGSERIAL PRIMARY KEY,
    chat_id    BIGINT NOT NULL,
    text       TEXT NOT NULL,
    retries    INT NOT NULL DEFAULT 0,
    next_retry_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint),
    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
);
CREATE TABLE IF NOT EXISTS create2_proxies (
    tg_id        BIGINT PRIMARY KEY,
    proxy_address TEXT NOT NULL,
    deployed     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_create2_proxies_addr ON create2_proxies (LOWER(proxy_address));
-- P2: Smart Wallet (ERC-4337) columns
ALTER TABLE users ADD COLUMN IF NOT EXISTS smart_address TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS smart_deployed BOOLEAN DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS smart_created_at BIGINT;
-- Web login replay protection: one-time wallet-login nonces (hash = PK).
CREATE TABLE IF NOT EXISTS login_nonces (
    nonce_hash TEXT PRIMARY KEY,
    created_at BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM now())::bigint)
);
"""
