from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Deliberately its own Settings class, not imported from
    tradeos-backend — this is a fully separate service. It shares two
    values with the main Zynost backend purely at the environment-variable
    level (same DATABASE_URL so merchant tables can reference real Zynost
    users; same JWT_SECRET so a Zynost login token this service receives
    can be verified without ever calling back into tradeos-backend's code),
    never via a Python import."""
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/tradeos"
    JWT_SECRET: str = ""

    PUBLIC_BASE_URL: str = "https://api.zynost.com"

    # --- Push notifications (Firebase Cloud Messaging) --- empty = pushes
    # silently skipped. Same Firebase project as tradeos-backend (one app,
    # one set of merchant/trader devices) - see push_notification_service.py.
    FIREBASE_PROJECT_ID: str = ""
    FIREBASE_SERVICE_ACCOUNT_JSON: str = ""

    # Zynost's OWN extended public key — used only to collect gateway
    # billing (Pro upgrades, volume-fee settlement) FROM other merchants,
    # via this same non-custodial checkout mechanism the gateway already
    # runs (see gateway_billing_service.py). Deliberately the SAME xpub
    # already configured as PAYMENT_EVM_XPUB on tradeos-backend for
    # Zynost's own subscription gateway — one real Zynost wallet, reused
    # here as plain public-key material (never a private key), so every
    # gateway-billing invoice is just one more watch-only child address of
    # a wallet Zynost already controls and already sweeps.
    ZYNOST_OWN_PAYOUT_XPUB: str = ""

    # --- Multi-RPC consensus (see app/services/rpc_consensus.py) ---
    # Each of these is a genuinely independent infrastructure operator —
    # the whole point is that no single one of them can single-handedly
    # fool a merchant's payment check. Empty = that provider is left out of
    # the pool for that chain; the free publicnode.com endpoint already in
    # payment_check.py always participates as a baseline, so this degrades
    # gracefully rather than breaking payments if you haven't signed up for
    # any of these yet.
    ALCHEMY_API_KEY: str = ""
    QUICKNODE_ETHEREUM_URL: str = ""       # full https://<name>.quiknode.pro/<token>/ URL from your QuickNode dashboard
    QUICKNODE_BSC_URL: str = ""
    QUICKNODE_POLYGON_URL: str = ""
    ANKR_API_KEY: str = ""                  # empty = falls back to Ankr's rate-limited free public endpoint

    # --- ERC-4337 gasless-checkout Paymaster (see ../zynost-paymaster/ for
    # the on-chain contract, app/services/paymaster_service.py for this
    # side). Empty PAYMASTER_CONTRACT_ADDRESS = gasless checkout disabled
    # entirely, same graceful-degradation convention as the rest of this
    # gateway's optional integrations. ---
    PAYMASTER_CONTRACT_ADDRESS: str = ""
    # A dedicated operational key whose ONLY power is signing gas-
    # sponsorship approvals the on-chain Paymaster contract verifies before
    # paying from ITS OWN deposit — this key has no relationship to, and no
    # access over, any user or merchant payment funds (see
    # payment_check.py's module docstring on this gateway's non-custodial
    # design). Compromise of this key alone is still bounded by the
    # contract's own on-chain per-sender/global daily caps.
    PAYMASTER_VERIFYING_SIGNER_PRIVATE_KEY: str = ""
    # The canonical ERC-4337 v0.6 EntryPoint — same address on every EVM
    # chain that has one deployed.
    PAYMASTER_ENTRYPOINT_ADDRESS: str = "0x5FF137D4b0FDCD49DcA30c7CF57E578a026d2789"
    PAYMASTER_RPC_URL: str = ""              # plain chain RPC, used for read-only eth_call (getHash/getDeposit/senderNonce)
    PAYMASTER_BUNDLER_RPC_URL: str = ""      # e.g. an Alchemy/Pimlico bundler endpoint, for eth_sendUserOperation
    PAYMASTER_CHAIN_ID: int = 11155111        # Sepolia by default — testnet-first, same convention as payment_service.py
    PAYMASTER_LOW_BALANCE_THRESHOLD_WEI: int = 10 ** 17  # 0.1 ETH — below this, check_gas_tank_balance() flags low_balance

    # --- Gasless checkout smart accounts (see app/services/
    # gasless_checkout_service.py) --- Empty = gasless checkout disabled
    # entirely (same graceful-degradation convention as the Paymaster
    # settings above). Deployed once, reused for every customer — each
    # customer's own smart account is a distinct counterfactual address
    # derived from (this factory, their own owner key, a fixed salt), never
    # a shared address.
    ACCOUNT_FACTORY_ADDRESS: str = ""

    # --- WalletConnect v2 (see static_src/src/wallet_connect.js) --- a
    # public dapp identifier, not a secret (same category as the Firebase
    # web API keys above) — free at cloud.reown.com. Empty = the
    # "WalletConnect" option is hidden from every wallet picker on this
    # checkout page entirely (same graceful-degradation convention as
    # every other optional integration here), leaving EIP-6963/injected
    # wallet detection as the only connect path, same as before this was
    # added.
    WALLETCONNECT_PROJECT_ID: str = ""

    # --- Email (business-profile-change verification codes — see
    # app/services/email_service.py) --- Own SMTP/Resend config, same
    # graceful-degradation convention as tradeos-backend's own email
    # setup (never a Python import from that service, per this class's
    # own "zero code relationship" rule above): tries SMTP first, then
    # Resend, then just logs the message so the request/verify flow keeps
    # working end-to-end (via server logs) even before real credentials
    # are supplied. Same physical mailbox as tradeos-backend can be reused
    # here by pointing these at the same values — it's just credentials,
    # not code.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 465         # 465 = implicit SSL, 587 = STARTTLS
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    RESEND_API_KEY: str = ""
    EMAIL_FROM: str = "Zynost Pay <noreply@zynost.umarae.com>"

    class Config:
        env_file = ".env"


settings = Settings()
