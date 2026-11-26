"""Bounded reads of approved operator context from its existing sources."""
import hashlib
from pathlib import Path

import yaml

PROFILE_REF = "icm:human-language-profile"
PROFILE_PATH = Path("/var/lib/ttros/contexts/human-language/"
                    "casey-linden_8f5be3f8/HUMAN_LANGUAGE_PROFILE_CURRENT.md")
PROFILE_ID = "HLP_casey-linden_8f5be3f8"


def read(reference):
    """Expose current writing preferences and their provenance, without sample material."""
    if reference != PROFILE_REF:
        raise ValueError("Unknown ICM reference.")
    with PROFILE_PATH.open("rb") as handle:
        data = handle.read(65537)
    if len(data) > 65536:
        raise ValueError("The indexed human language profile exceeds its read limit.")
    try:
        _, header, _ = data.decode("utf-8").split("---\n", 2)
        profile = yaml.safe_load(header)
        if (profile["artifact_kind"] != "HUMAN_LANGUAGE_PROFILE" or profile["profile_id"] != PROFILE_ID
                or profile["status"] != "current" or profile["schema_version"] != 2):
            raise ValueError("Profile identity, version or status changed.")
        preferences = profile["explicit_current_preferences"]
        content = "\n".join(f"- {row['context_channel_scope']}: {row['desired_behaviour']}" for row in preferences)
        if not content:
            raise ValueError("The profile has no explicit preferences.")
        return {"reference": reference, "content": content, "source": profile["canonical_current_reference"],
                "profile_id": PROFILE_ID, "revision": profile["profile_revision"], "updated": profile["updated_at"],
                "sha256": hashlib.sha256(data).hexdigest(), "authority": "operator_writing_preferences"}
    except (ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise ValueError("The indexed human language profile could not be verified.") from exc
