import json
import hashlib
import structlog

try:
    import weaviate
    import weaviate.classes as wvc
    from weaviate.collections import Collection
    _HAS_WEAVIATE = True
except ImportError:
    weaviate = None  # type: ignore
    wvc = None  # type: ignore
    Collection = None  # type: ignore
    _HAS_WEAVIATE = False

from app.config import settings

log = structlog.get_logger()

CASES_CLASS = "ClaimCase"
FRAUD_PATTERNS_CLASS = "FraudPattern"


class VectorStore:
    """Weaviate vector store for case similarity and fraud pattern retrieval."""

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if not _HAS_WEAVIATE:
            return None
        if self._client is None or not self._client.is_connected():
            self._client = weaviate.connect_to_local(
                host=settings.weaviate_url.replace("http://", "").split(":")[0],
                port=int(settings.weaviate_url.split(":")[-1]),
                auth_credentials=wvc.init.Auth.api_key(settings.weaviate_api_key) if settings.weaviate_api_key else None,
            )
        return self._client

    def _ensure_schema(self) -> None:
        client = self._get_client()
        if client is None:
            return
        existing = [c.name for c in client.collections.list_all().values()]

        if CASES_CLASS not in existing:
            client.collections.create(
                name=CASES_CLASS,
                vectorizer_config=wvc.config.Configure.Vectorizer.none(),
                properties=[
                    wvc.config.Property(name="claim_id", data_type=wvc.config.DataType.TEXT),
                    wvc.config.Property(name="claim_number", data_type=wvc.config.DataType.TEXT),
                    wvc.config.Property(name="verdict", data_type=wvc.config.DataType.TEXT),
                    wvc.config.Property(name="icd_codes", data_type=wvc.config.DataType.TEXT),
                    wvc.config.Property(name="cpt_codes", data_type=wvc.config.DataType.TEXT),
                    wvc.config.Property(name="provider_id", data_type=wvc.config.DataType.TEXT),
                    wvc.config.Property(name="fraud_score", data_type=wvc.config.DataType.NUMBER),
                    wvc.config.Property(name="risk_score", data_type=wvc.config.DataType.NUMBER),
                    wvc.config.Property(name="summary", data_type=wvc.config.DataType.TEXT),
                    wvc.config.Property(name="full_case", data_type=wvc.config.DataType.TEXT),
                ],
            )
            log.info("weaviate_schema_created", collection=CASES_CLASS)

        if FRAUD_PATTERNS_CLASS not in existing:
            client.collections.create(
                name=FRAUD_PATTERNS_CLASS,
                vectorizer_config=wvc.config.Configure.Vectorizer.none(),
                properties=[
                    wvc.config.Property(name="pattern_id", data_type=wvc.config.DataType.TEXT),
                    wvc.config.Property(name="pattern_name", data_type=wvc.config.DataType.TEXT),
                    wvc.config.Property(name="description", data_type=wvc.config.DataType.TEXT),
                    wvc.config.Property(name="indicators", data_type=wvc.config.DataType.TEXT),
                    wvc.config.Property(name="severity", data_type=wvc.config.DataType.TEXT),
                ],
            )
            log.info("weaviate_schema_created", collection=FRAUD_PATTERNS_CLASS)

    def _build_case_text(self, case_data: dict) -> str:
        """Build searchable text representation of a case."""
        parts = [
            f"Diagnosis: {case_data.get('diagnosis_description', '')}",
            f"Procedure: {case_data.get('procedure_description', '')}",
            f"ICD: {' '.join(case_data.get('icd_codes', []))}",
            f"CPT: {' '.join(case_data.get('cpt_codes', []))}",
            f"Provider: {case_data.get('provider_name', '')}",
            f"Amount: {case_data.get('billed_amount', 0)}",
            f"Verdict: {case_data.get('verdict', '')}",
        ]
        return " | ".join(parts)

    def _fake_embed(self, text: str) -> list[float]:
        """Deterministic pseudo-embedding for dev/test without embedding API."""
        h = hashlib.sha256(text.encode()).digest()
        return [((b - 128) / 128.0) for b in h[:128]] + [0.0] * (1536 - 128)

    def store_case(self, case_data: dict, embedding: list[float] | None = None) -> str:
        try:
            self._ensure_schema()
            client = self._get_client()
            collection: Collection = client.collections.get(CASES_CLASS)

            text = self._build_case_text(case_data)
            vector = embedding or self._fake_embed(text)

            uuid = collection.data.insert(
                properties={
                    "claim_id": case_data.get("claim_id", ""),
                    "claim_number": case_data.get("claim_number", ""),
                    "verdict": case_data.get("verdict", ""),
                    "icd_codes": " ".join(case_data.get("icd_codes", [])),
                    "cpt_codes": " ".join(case_data.get("cpt_codes", [])),
                    "provider_id": case_data.get("provider_id", ""),
                    "fraud_score": float(case_data.get("fraud_score", 0.0)),
                    "risk_score": float(case_data.get("risk_score", 0.0)),
                    "summary": text[:1000],
                    "full_case": json.dumps(case_data)[:8000],
                },
                vector=vector,
            )
            log.info("case_stored_in_vector_db", claim_id=case_data.get("claim_id"))
            return str(uuid)
        except Exception as e:
            log.error("vector_store_error", action="store_case", error=str(e))
            return ""

    def find_similar_cases(self, query_text: str, limit: int = 5, embedding: list[float] | None = None) -> list[dict]:
        try:
            self._ensure_schema()
            client = self._get_client()
            collection: Collection = client.collections.get(CASES_CLASS)

            vector = embedding or self._fake_embed(query_text)
            results = collection.query.near_vector(
                near_vector=vector,
                limit=limit,
                return_properties=["claim_id", "verdict", "fraud_score", "risk_score", "summary", "full_case"],
            )

            cases = []
            for obj in results.objects:
                try:
                    full = json.loads(obj.properties.get("full_case", "{}"))
                    cases.append(full)
                except Exception:
                    cases.append(dict(obj.properties))
            return cases
        except Exception as e:
            log.error("vector_store_error", action="find_similar", error=str(e))
            return []

    def find_fraud_patterns(self, query_text: str, limit: int = 3) -> list[dict]:
        try:
            self._ensure_schema()
            client = self._get_client()
            collection: Collection = client.collections.get(FRAUD_PATTERNS_CLASS)
            vector = self._fake_embed(query_text)
            results = collection.query.near_vector(near_vector=vector, limit=limit)
            return [dict(obj.properties) for obj in results.objects]
        except Exception as e:
            log.error("vector_store_error", action="find_fraud_patterns", error=str(e))
            return []

    def close(self) -> None:
        if self._client:
            self._client.close()


vector_store = VectorStore()
