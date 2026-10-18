from ..storage.database import encode
from .profiles import Profile, presets, validate_baseline


class StrategyStore:
    def __init__(self, database):
        self.db = database
        with self.db.transaction():
            self.db.connection.execute(
                "CREATE TABLE IF NOT EXISTS strategy_profiles(name TEXT, version INTEGER, data TEXT NOT NULL, PRIMARY KEY(name,version))"
            )
            self.db.connection.execute(
                "CREATE TABLE IF NOT EXISTS strategy_runtime(key TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
        with self.db.transaction():
            for profile in presets():
                self.db.connection.execute(
                    "INSERT OR IGNORE INTO strategy_profiles VALUES(?,?,?)",
                    (profile.name, profile.version, encode(profile.model_dump(mode="json"))),
                )
            if self.read("control") is None:
                self.write(
                    "control",
                    {
                        "enabled": False,
                        "emergency": False,
                        "profile_name": "CUSTOM V2",
                        "profile_version": 1,
                    },
                )

    def read(self, key):
        return self.db.one("SELECT data FROM strategy_runtime WHERE key=?", (key,))

    def write(self, key, value):
        self.db.connection.execute(
            "INSERT INTO strategy_runtime VALUES(?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
            (key, encode(value)),
        )

    def get_profile(self, name, version):
        data = self.db.one(
            "SELECT data FROM strategy_profiles WHERE name=? AND version=?", (name, version)
        )
        if data is None:
            raise ValueError("Unknown strategy profile/version")
        return Profile.model_validate(data)

    def profiles(self):
        return self.db.many("SELECT data FROM strategy_profiles ORDER BY name,version")

    def save_profile(self, value):
        profile = Profile.model_validate(value)
        validate_baseline(profile)
        with self.db.transaction():
            old = self.db.one(
                "SELECT data FROM strategy_profiles WHERE name=? AND version=?",
                (profile.name, profile.version),
            )
            if old is not None:
                if Profile.model_validate(old) == profile:
                    return profile
                raise ValueError("Versions are immutable; increment the version")
            versions = self.db.connection.execute(
                "SELECT MAX(version) FROM strategy_profiles WHERE name=?", (profile.name,)
            ).fetchone()[0]
            if versions is not None and profile.version <= versions:
                raise ValueError("New versions must increase monotonically")
            self.db.connection.execute(
                "INSERT INTO strategy_profiles VALUES(?,?,?)",
                (profile.name, profile.version, encode(profile.model_dump(mode="json"))),
            )
        return profile
