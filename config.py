import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    SUPABASE_URL: str = os.getenv("MEMORYOS_SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("MEMORYOS_SUPABASE_KEY", "")
    UPSTASH_REDIS_REST_URL: str = os.getenv("UPSTASH_REDIS_REST_URL", "")
    UPSTASH_REDIS_REST_TOKEN: str = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    MEMORYOS_SECRET: str = os.getenv("MEMORYOS_SECRET", "")
    PORT: int = int(os.getenv("PORT", "8000"))
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    SIMILARITY_THRESHOLD: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.20"))
    DEDUP_THRESHOLD: float = 0.92
    CONTRADICTION_THRESHOLD: float = 0.85
    CRITICAL_MEMORY_CAP: int = 5
    MAX_CONTEXT_CHARS: int = 2000
    CACHE_TTL_SECONDS: int = 60
    WEIGHT_SEMANTIC: float = 0.50
    WEIGHT_IMPORTANCE: float = 0.20
    WEIGHT_RECENCY: float = 0.15
    WEIGHT_OUTCOME: float = 0.15

    def validate(self):
        missing = []
        required = [
            ("MEMORYOS_SUPABASE_URL", self.SUPABASE_URL),
            ("MEMORYOS_SUPABASE_KEY", self.SUPABASE_KEY),
            ("UPSTASH_REDIS_REST_URL", self.UPSTASH_REDIS_REST_URL),
            ("UPSTASH_REDIS_REST_TOKEN", self.UPSTASH_REDIS_REST_TOKEN),
            ("ANTHROPIC_API_KEY", self.ANTHROPIC_API_KEY),
            ("MEMORYOS_SECRET", self.MEMORYOS_SECRET),
        ]
        for name, value in required:
            if not value:
                missing.append(name)
        if missing:
            raise ValueError(f"MemoryOS cannot start. Missing: {', '.join(missing)}")
        return True

config = Config()