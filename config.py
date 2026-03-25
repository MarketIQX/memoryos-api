import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Supabase
    SUPABASE_URL: str = os.getenv("MEMORYOS_SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("MEMORYOS_SUPABASE_KEY", "")

    # Voyage AI
    VOYAGE_API_KEY: str = os.getenv("VOYAGE_API_KEY", "")

    # Upstash Redis
    UPSTASH_REDIS_REST_URL: str = os.getenv("UPSTASH_REDIS_REST_URL", "")
    UPSTASH_REDIS_REST_TOKEN: str = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

    # Anthropic
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

    # App
    MEMORYOS_SECRET: str = os.getenv("MEMORYOS_SECRET", "")
    PORT: int = int(os.getenv("PORT", "8000"))
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

    # Memory thresholds — every value is justified in the technical documentation
    SIMILARITY_THRESHOLD: float = 0.30      # minimum cosine similarity to return a memory
    DEDUP_THRESHOLD: float = 0.92           # above this: update existing, not insert new
    CONTRADICTION_THRESHOLD: float = 0.85   # above this + opposing sentiment: flag contradiction
    CRITICAL_MEMORY_CAP: int = 5            # max critical memories injected per context call
    MAX_CONTEXT_CHARS: int = 2000           # max characters in synthesised context block
    CACHE_TTL_SECONDS: int = 60             # Redis cache TTL for /context results
    IMPORTANCE_VOYAGE_THRESHOLD: float = 0.7  # above this: use Voyage AI, below: use local

    # Composite scoring weights — Law 4: memory must compound
    # 0.50 semantic + 0.20 importance + 0.15 recency + 0.15 outcome = 1.00
    WEIGHT_SEMANTIC: float = 0.50
    WEIGHT_IMPORTANCE: float = 0.20
    WEIGHT_RECENCY: float = 0.15
    WEIGHT_OUTCOME: float = 0.15

    def validate(self):
        missing = []
        required = [
            ("MEMORYOS_SUPABASE_URL", self.SUPABASE_URL),
            ("MEMORYOS_SUPABASE_KEY", self.SUPABASE_KEY),
            ("VOYAGE_API_KEY", self.VOYAGE_API_KEY),
            ("UPSTASH_REDIS_REST_URL", self.UPSTASH_REDIS_REST_URL),
            ("UPSTASH_REDIS_REST_TOKEN", self.UPSTASH_REDIS_REST_TOKEN),
            ("ANTHROPIC_API_KEY", self.ANTHROPIC_API_KEY),
            ("MEMORYOS_SECRET", self.MEMORYOS_SECRET),
        ]
        for name, value in required:
            if not value:
                missing.append(name)
        if missing:
            raise ValueError(
                f"MemoryOS cannot start. Missing environment variables: "
                f"{', '.join(missing)}. "
                f"Check your Railway environment variables."
            )
        return True


config = Config()
