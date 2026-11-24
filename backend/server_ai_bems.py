#!/usr/bin/env python3
"""BEMS backend for one real PZEM room plus nineteen simulated rooms.

The data pipeline stores an explicit source/simulated tag, runs Isolation Forest
on each room stream, forecasts the hybrid demo aggregate with TensorFlow LSTM,
calculates EVN tariff estimates and publishes dry-run Demand Response commands.
No command from this project switches a physical relay or electrical load.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

# TensorFlow is optional at startup so telemetry, tariffs and anomaly alerts
# continue working if the heavy ML package has not yet been installed.
try:
    from tensorflow.keras import Sequential
    from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau
    from tensorflow.keras.layers import LSTM, Dense, Dropout
    TENSORFLOW_AVAILABLE = True
except Exception as import_error:  # pragma: no cover - depends on runtime image
    TENSORFLOW_AVAILABLE = False
    logging.getLogger(__name__).warning("TensorFlow is unavailable: %s", import_error)


# ------------------------------- CONFIGURATION --------------------------------

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
load_dotenv(PROJECT_DIR / ".env")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
)
LOGGER = logging.getLogger("bems-ai")

TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh"))
MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_TELEMETRY_TOPIC = os.getenv("MQTT_TELEMETRY_TOPIC", "bems/telemetry")
MQTT_COMMAND_PREFIX = os.getenv("MQTT_COMMAND_PREFIX", "bems/commands")
MQTT_ALERT_TOPIC = os.getenv("MQTT_ALERT_TOPIC", "bems/alerts")

INFLUX_URL = os.getenv("INFLUX_URL", "http://127.0.0.1:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "replace-this-token")
INFLUX_ORG = os.getenv("INFLUX_ORG", "bems-org")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "bems")

TARIFF_PROFILE = os.getenv("TARIFF_PROFILE", "education_lv").strip().lower()
VAT_RATE = float(os.getenv("VAT_RATE", "0.0"))  # Official tariff figures exclude VAT.
NIGHT_HIGH_POWER_W = float(os.getenv("NIGHT_HIGH_POWER_W", "1500"))
ANOMALY_MIN_SAMPLES = int(os.getenv("ANOMALY_MIN_SAMPLES", "200"))
ANOMALY_TRAIN_INTERVAL_SECONDS = int(os.getenv("ANOMALY_TRAIN_INTERVAL_SECONDS", "300"))
ANOMALY_ALERT_COOLDOWN_SECONDS = int(os.getenv("ANOMALY_ALERT_COOLDOWN_SECONDS", "900"))

DR_ENABLED = os.getenv("DR_ENABLED", "true").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
DR_BUILDING_LIMIT_W = float(os.getenv("DR_BUILDING_LIMIT_W", "6500"))
DR_SHEDTABLE_ROOM_IDS = {
    int(item.strip()) for item in os.getenv("DR_SHEDTABLE_ROOM_IDS", "17,18,19,20").split(",")
    if item.strip().isdigit()
}
DR_ESTIMATE_MINUTES = int(os.getenv("DR_ESTIMATE_MINUTES", "15"))
DR_COMMAND_COOLDOWN_SECONDS = int(os.getenv("DR_COMMAND_COOLDOWN_SECONDS", "1800"))
DR_VIRTUAL_REDUCTION_PERCENT = float(os.getenv("DR_VIRTUAL_REDUCTION_PERCENT", "75"))

MODEL_DIR = Path(os.getenv("MODEL_DIR", str(PROJECT_DIR / "models")))
MODEL_DIR.mkdir(parents=True, exist_ok=True)
LSTM_MODEL_PATH = MODEL_DIR / "bems_lstm_24h.keras"
LSTM_SCALER_PATH = MODEL_DIR / "bems_lstm_scaler.joblib"
LSTM_LOOKBACK_HOURS = 48
LSTM_HORIZON_HOURS = 24
LSTM_MIN_HISTORY_HOURS = int(os.getenv("LSTM_MIN_HISTORY_HOURS", "720"))  # 30 days.
LSTM_RETRAIN_SECONDS = int(os.getenv("LSTM_RETRAIN_SECONDS", "43200"))

LOCAL_TIME_PERIODS = {
    "off_peak": "00:00-06:00 every day",
    "peak": "17:30-22:30 Monday-Saturday",
    "normal": "other hours; Sunday has no peak period",
}


# ------------------------------- EVN TARIFFS ----------------------------------

# Decision 2892/QD-BCT tariff profiles effective from 01-Nov-2026.
# All prices below are VND/kWh and exclude VAT, as shown in the published tariff.
# NOTE: five-tier household pricing and time-of-use pricing are separate cases.
RESIDENTIAL_TIERS = [
    (100.0, 1901.0),
    (100.0, 2289.0),
    (200.0, 2894.0),
    (300.0, 3456.0),
    (math.inf, 3844.0),
]

TARIFF_PROFILES: dict[str, dict[str, Any]] = {
    "education_lv": {"kind": "flat", "price": 2030.0},
    "education_mv": {"kind": "flat", "price": 1901.0},
    "public_service_lv": {"kind": "flat", "price": 2181.0},
    "public_service_mv": {"kind": "flat", "price": 2095.0},
    "production_lv": {"kind": "tou", "normal": 1966.0, "off_peak": 1274.0, "peak": 3586.0},
    "production_mv": {"kind": "tou", "normal": 1814.0, "off_peak": 1166.0, "peak": 3348.0},
    "commercial_other_lv": {"kind": "tou", "normal": 3089.0, "off_peak": 1880.0, "peak": 5313.0},
    "commercial_other_mv": {"kind": "tou", "normal": 2829.0, "off_peak": 1577.0, "peak": 4924.0},
    "tourist_accommodation_lv": {"kind": "tou", "normal": 1966.0, "off_peak": 1274.0, "peak": 3586.0},
    "residential": {"kind": "progressive", "tiers": RESIDENTIAL_TIERS},
}


def localize_timestamp(value: datetime) -> datetime:
    """Return a timezone-aware Vietnam-local datetime."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(TZ)


def classify_time_period(timestamp: datetime) -> str:
    """Classify tariff time-of-use using Decision 963/QD-BCT time windows."""
    local_time = localize_timestamp(timestamp)
    minute_of_day = local_time.hour * 60 + local_time.minute
    if minute_of_day < 6 * 60:
        return "off_peak"
    is_monday_to_saturday = local_time.weekday() <= 5
    if is_monday_to_saturday and (17 * 60 + 30) <= minute_of_day < (22 * 60 + 30):
        return "peak"
    return "normal"


def progressive_cost_for_increment(kwh_increment: float, month_to_date_kwh: float) -> float:
    """Price a new kWh increment against the five household consumption tiers."""
    remaining = max(0.0, float(kwh_increment))
    consumed = max(0.0, float(month_to_date_kwh))
    cost = 0.0
    tier_start = 0.0
    for tier_width, price in RESIDENTIAL_TIERS:
        tier_end = tier_start + tier_width
        if consumed >= tier_end:
            tier_start = tier_end
            continue
        available_in_tier = tier_end - consumed
        portion = min(remaining, available_in_tier)
        cost += portion * price
        remaining -= portion
        consumed += portion
        if remaining <= 1e-12:
            break
        tier_start = tier_end
    return cost


def tariff_price_vnd_per_kwh(
    timestamp: datetime, profile: str | None = None, month_to_date_kwh: float = 0.0
) -> float:
    """Return the active or marginal tariff rate for a selected customer profile."""
    selected = (profile or TARIFF_PROFILE).lower()
    if selected not in TARIFF_PROFILES:
        raise ValueError(f"Unknown TARIFF_PROFILE={selected!r}")
    details = TARIFF_PROFILES[selected]
    if details["kind"] == "progressive":
        # For residential use, return the marginal price of the next kWh based
        # on the consumption tier already reached in the current billing month.
        consumed = max(0.0, float(month_to_date_kwh))
        tier_end = 0.0
        for width, price in details["tiers"]:
            tier_end += width
            if consumed < tier_end:
                return float(price)
        return float(details["tiers"][-1][1])
    if details["kind"] == "flat":
        return float(details["price"])
    period = classify_time_period(timestamp)
    return float(details[period])


def calculate_incremental_cost(
    kwh_increment: float, timestamp: datetime, month_to_date_kwh: float,
    profile: str | None = None,
) -> float:
    """Calculate VND cost for an incremental kWh sample; VAT is optional."""
    selected = (profile or TARIFF_PROFILE).lower()
    if selected not in TARIFF_PROFILES:
        raise ValueError(f"Unknown TARIFF_PROFILE={selected!r}")
    details = TARIFF_PROFILES[selected]
    if details["kind"] == "progressive":
        base_cost = progressive_cost_for_increment(kwh_increment, month_to_date_kwh)
    elif details["kind"] == "flat":
        base_cost = kwh_increment * float(details["price"])
    else:
        period = classify_time_period(timestamp)
        base_cost = kwh_increment * float(details[period])
    return base_cost * (1.0 + VAT_RATE)


# ------------------------------- DATA HELPERS ---------------------------------

@dataclass
class RoomReading:
    room_id: int
    voltage_v: float
    current_a: float
    power_w: float
    energy_kwh: float
    power_factor: float
    frequency_hz: float
    valid: bool
    simulated: bool


def parse_timestamp(payload: dict[str, Any]) -> datetime:
    """Read Unix milliseconds from firmware; fall back to server time."""
    raw_ms = payload.get("timestamp_epoch_ms", 0)
    try:
        raw_ms = int(raw_ms)
        if raw_ms > 1_700_000_000_000:
            return datetime.fromtimestamp(raw_ms / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        pass
    return datetime.now(timezone.utc)


def parse_room(row: dict[str, Any]) -> RoomReading:
    """Validate and normalize one JSON room measurement."""
    room_id = int(row["id"])
    if not 1 <= room_id <= 20:
        raise ValueError(f"room id {room_id} is outside the expected 1..20 range")
    values = {
        "voltage_v": float(row.get("voltage_v", 0.0)),
        "current_a": float(row.get("current_a", 0.0)),
        "power_w": float(row.get("power_w", 0.0)),
        "energy_kwh": float(row.get("energy_kwh", 0.0)),
        "power_factor": float(row.get("power_factor", 0.0)),
        "frequency_hz": float(row.get("frequency_hz", 0.0)),
    }
    if any(not np.isfinite(value) for value in values.values()):
        raise ValueError(f"room {room_id} contains a non-finite measurement")
    if values["voltage_v"] < 0 or values["current_a"] < 0 or values["power_w"] < 0:
        raise ValueError(f"room {room_id} contains a negative physical quantity")
    simulated_value = row.get("simulated")
    valid_value = row.get("valid")
    if not isinstance(simulated_value, bool):
        raise ValueError(f"room {room_id} must include a boolean 'simulated' field")
    if not isinstance(valid_value, bool):
        raise ValueError(f"room {room_id} must include a boolean 'valid' field")

    expected_source = "synthetic_generator" if simulated_value else "pzem004t_v3"
    if row.get("source") != expected_source:
        raise ValueError(
            f"room {room_id} source must be {expected_source!r} when "
            f"simulated={str(simulated_value).lower()}"
        )

    return RoomReading(
        room_id=room_id,
        **values,
        valid=valid_value,
        simulated=simulated_value,
    )


def make_feature_vector(reading: RoomReading, timestamp: datetime) -> np.ndarray:
    """Build features for unsupervised room-level behavior modelling."""
    local_time = localize_timestamp(timestamp)
    hour_angle = 2.0 * math.pi * (local_time.hour + local_time.minute / 60.0) / 24.0
    return np.array([
        reading.power_w,
        reading.current_a,
        reading.voltage_v,
        reading.power_factor,
        math.sin(hour_angle),
        math.cos(hour_angle),
    ], dtype=np.float32)


# -------------------------------- AI ENGINE -----------------------------------

class BemsAIEngine:
    """Owns per-room anomaly models and the 24-hour load forecasting model."""

    def __init__(self) -> None:
        self.room_samples: dict[int, deque[np.ndarray]] = defaultdict(
            lambda: deque(maxlen=5000)
        )
        self.room_models: dict[int, Pipeline] = {}
        self.room_last_fit: dict[int, float] = defaultdict(float)
        self.last_lstm_train = 0.0
        self.last_anomaly_train = 0.0
        self.lock = threading.RLock()

    def observe_room(self, reading: RoomReading, timestamp: datetime) -> tuple[bool, str]:
        """Append one sample, score it if a model exists and apply a night rule."""
        vector = make_feature_vector(reading, timestamp).reshape(1, -1)
        anomaly = False
        reason = ""
        local_time = localize_timestamp(timestamp)

        with self.lock:
            model = self.room_models.get(reading.room_id)
            if model is not None:
                try:
                    prediction = int(model.predict(vector)[0])
                    if prediction < 0:
                        anomaly = True
                        reason = "isolation_forest_outlier"
                except Exception:
                    LOGGER.exception("Anomaly inference failed for room %d", reading.room_id)
            self.room_samples[reading.room_id].append(vector[0])

        # This is an operational energy-use alert, not an electrical fault diagnosis.
        is_night = 0 <= local_time.hour < 6
        if is_night and reading.power_w >= NIGHT_HIGH_POWER_W:
            anomaly = True
            reason = "high_power_during_off_peak_window"
        return anomaly, reason

    def train_anomaly_models_if_due(self) -> None:
        """Fit Isolation Forest per room after sufficient observations exist."""
        now = time.time()
        if now - self.last_anomaly_train < ANOMALY_TRAIN_INTERVAL_SECONDS:
            return
        trained = 0
        with self.lock:
            for room_id, sample_queue in self.room_samples.items():
                if len(sample_queue) < ANOMALY_MIN_SAMPLES:
                    continue
                matrix = np.asarray(sample_queue, dtype=np.float32)
                pipeline = Pipeline([
                    ("scale", StandardScaler()),
                    ("isolation_forest", IsolationForest(
                        n_estimators=150,
                        contamination="auto",
                        random_state=42,
                        n_jobs=-1,
                    )),
                ])
                pipeline.fit(matrix)
                self.room_models[room_id] = pipeline
                self.room_last_fit[room_id] = now
                trained += 1
        self.last_anomaly_train = now
        if trained:
            LOGGER.info("Isolation Forest models fitted for %d rooms", trained)

    def query_hourly_building_energy(self, query_api) -> pd.Series:
        """Return hybrid demo hourly energy (one PZEM + 19 synthetic room streams).

        The forecast is explicitly a hybrid-demo forecast. It must not be reported
        as the measured load forecast of a fully instrumented 20-room building.
        """
        flux = f'''from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -90d)
  |> filter(fn: (r) => r._measurement == "bems_room" and r._field == "power_w")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> group(columns: ["_time"])
  |> sum(column: "_value")
  |> sort(columns: ["_time"])
'''
        tables = query_api.query(flux, org=INFLUX_ORG)
        values: list[tuple[datetime, float]] = []
        for table in tables:
            for record in table.records:
                values.append((record.get_time(), float(record.get_value()) / 1000.0))
        if not values:
            return pd.Series(dtype=float)
        frame = pd.DataFrame(values, columns=["timestamp", "kwh"])
        frame = frame.dropna().drop_duplicates(subset=["timestamp"], keep="last")
        frame = frame.set_index(pd.to_datetime(frame["timestamp"], utc=True))["kwh"]
        frame = frame.sort_index().resample("1h").mean()
        # Fill only short telemetry gaps. Long gaps remain missing and are dropped.
        frame = frame.interpolate(method="time", limit=2).dropna()
        return frame.astype(float)

    def train_and_forecast_lstm(self, influx_query_api, influx_write_api) -> dict[str, Any] | None:
        """Train a multi-output LSTM and write the next 24 hourly forecasts."""
        if not TENSORFLOW_AVAILABLE:
            LOGGER.warning("TensorFlow not installed; LSTM forecasting is disabled.")
            return None
        series = self.query_hourly_building_energy(influx_query_api)
        if len(series) < LSTM_MIN_HISTORY_HOURS:
            LOGGER.info(
                "Hybrid-demo LSTM waiting for hourly history: %d/%d points available.",
                len(series), LSTM_MIN_HISTORY_HOURS,
            )
            return None

        from sklearn.preprocessing import MinMaxScaler

        values = series.to_numpy(dtype=np.float32)
        scaler = MinMaxScaler(feature_range=(0.0, 1.0))
        # Split the raw time series first. The scaler is fitted only on the
        # training period; validation values cannot influence its parameters.
        raw_train_end = int(len(values) * 0.80)
        if raw_train_end <= LSTM_LOOKBACK_HOURS + LSTM_HORIZON_HOURS:
            LOGGER.warning("Not enough pre-validation history for LSTM training.")
            return None
        scaler.fit(values[:raw_train_end].reshape(-1, 1))
        scaled = scaler.transform(values.reshape(-1, 1)).astype(np.float32).flatten()

        x_train_rows: list[np.ndarray] = []
        y_train_rows: list[np.ndarray] = []
        x_val_rows: list[np.ndarray] = []
        y_val_rows: list[np.ndarray] = []
        end = len(scaled) - LSTM_LOOKBACK_HOURS - LSTM_HORIZON_HOURS + 1
        for start in range(max(0, end)):
            target_start = start + LSTM_LOOKBACK_HOURS
            target_end = target_start + LSTM_HORIZON_HOURS
            input_window = scaled[start:target_start]
            target_window = scaled[target_start:target_end]
            # Keep every validation target strictly after the training period.
            if target_end <= raw_train_end:
                x_train_rows.append(input_window)
                y_train_rows.append(target_window)
            elif target_start >= raw_train_end:
                x_val_rows.append(input_window)
                y_val_rows.append(target_window)

        if len(x_train_rows) < 50 or len(x_val_rows) < 12:
            LOGGER.warning(
                "Not enough non-overlapping LSTM windows: train=%d, validation=%d",
                len(x_train_rows), len(x_val_rows),
            )
            return None

        x_train = np.asarray(x_train_rows, dtype=np.float32).reshape(
            -1, LSTM_LOOKBACK_HOURS, 1
        )
        y_train = np.asarray(y_train_rows, dtype=np.float32)
        x_val = np.asarray(x_val_rows, dtype=np.float32).reshape(
            -1, LSTM_LOOKBACK_HOURS, 1
        )
        y_val = np.asarray(y_val_rows, dtype=np.float32)

        model = Sequential([
            LSTM(64, return_sequences=True, input_shape=(LSTM_LOOKBACK_HOURS, 1)),
            Dropout(0.20),
            LSTM(32),
            Dropout(0.15),
            Dense(32, activation="relu"),
            Dense(LSTM_HORIZON_HOURS),
        ])
        model.compile(optimizer="adam", loss="mae")
        callbacks = [
            EarlyStopping(monitor="val_loss", patience=7, restore_best_weights=True),
            ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=3, min_lr=1e-5),
        ]
        model.fit(
            x_train, y_train,
            validation_data=(x_val, y_val),
            epochs=40,
            batch_size=32,
            shuffle=False,
            callbacks=callbacks,
            verbose=0,
        )

        # Convert validation values back to kWh before calculating MAPE.
        val_prediction_scaled = model.predict(x_val, verbose=0)
        y_val_actual = scaler.inverse_transform(y_val.reshape(-1, 1)).reshape(y_val.shape)
        val_prediction = scaler.inverse_transform(
            val_prediction_scaled.reshape(-1, 1)
        ).reshape(val_prediction_scaled.shape)
        denominator = np.maximum(np.abs(y_val_actual), 0.05)  # Avoid division by zero for near-idle hours.
        mape = float(np.mean(np.abs((y_val_actual - val_prediction) / denominator)) * 100.0)

        # Forecast the next day from the latest 48 hourly observations.
        last_window = scaled[-LSTM_LOOKBACK_HOURS:].reshape(1, LSTM_LOOKBACK_HOURS, 1)
        prediction_scaled = model.predict(last_window, verbose=0).flatten()
        forecast_kwh = scaler.inverse_transform(prediction_scaled.reshape(-1, 1)).flatten()
        forecast_kwh = np.maximum(forecast_kwh, 0.0)

        model.save(LSTM_MODEL_PATH)
        joblib.dump(scaler, LSTM_SCALER_PATH)

        last_hour = pd.Timestamp(series.index[-1]).to_pydatetime()
        if last_hour.tzinfo is None:
            last_hour = last_hour.replace(tzinfo=timezone.utc)
        for offset, energy in enumerate(forecast_kwh, start=1):
            forecast_time = last_hour + timedelta(hours=offset)
            point = (
                Point("bems_forecast")
                .tag("model", "LSTM_48h_to_24h")
                .tag("forecast_scope", "hybrid_1_real_19_simulated")
                .tag("physical_room_count", "1")
                .tag("simulated_room_count", "19")
                .field("forecast_kwh", float(energy))
                .field("validation_mape_pct", mape)
                .field("mape_target_pct", 5.0)
                .time(forecast_time, WritePrecision.S)
            )
            influx_write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)

        self.last_lstm_train = time.time()
        LOGGER.info(
            "LSTM updated on HYBRID demo data (one PZEM + 19 simulated). "
            "Held-out MAPE=%.2f%%; 24 points written. This is not a measured-building MAPE.",
            mape,
        )
        return {
            "validation_mape_pct": mape,
            "forecast_points": len(forecast_kwh),
            "forecast_scope": "hybrid_1_real_19_simulated",
        }


# ------------------------------- APP SERVICE ----------------------------------

class BemsService:
    """Coordinates MQTT input, Influx writes, alerts, tariff costs and DR demo."""

    def __init__(self) -> None:
        self.influx = InfluxDBClient(
            url=INFLUX_URL,
            token=INFLUX_TOKEN,
            org=INFLUX_ORG,
            timeout=10_000,
        )
        self.write_api = self.influx.write_api(write_options=SYNCHRONOUS)
        self.query_api = self.influx.query_api()
        self.ai = BemsAIEngine()
        self.last_room_sample: dict[int, tuple[datetime, float]] = {}
        self.month_to_date_kwh = 0.0
        self.month_key = datetime.now(TZ).strftime("%Y-%m")
        self.month_cost_vnd = 0.0
        self.last_alert_at: dict[tuple[int, str], float] = {}
        self.last_dr_at: dict[int, float] = {}
        self.lock = threading.RLock()
        self.last_lstm_attempt = 0.0
        self.lstm_training = False
        self.lstm_lock = threading.Lock()

        self.mqtt_client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"bems-ai-{int(time.time())}",
            clean_session=True,
        )
        if MQTT_USERNAME:
            self.mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message
        self.mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)

    def on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code == 0:
            LOGGER.info("Connected to MQTT %s:%d", MQTT_HOST, MQTT_PORT)
            client.subscribe(MQTT_TELEMETRY_TOPIC, qos=0)
        else:
            LOGGER.error("MQTT connection rejected: %s", reason_code)

    def on_message(self, client, userdata, message) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            self.process_telemetry(payload)
        except Exception:
            LOGGER.exception("Failed to process MQTT message on %s", message.topic)

    def reset_month_if_needed(self, timestamp: datetime) -> None:
        current_key = localize_timestamp(timestamp).strftime("%Y-%m")
        if current_key != self.month_key:
            self.month_key = current_key
            self.month_to_date_kwh = 0.0
            self.month_cost_vnd = 0.0

    def write_room_measurement(self, reading: RoomReading, timestamp: datetime) -> None:
        point = (
            Point("bems_room")
            .tag("room_id", str(reading.room_id))
            .tag("simulated", str(reading.simulated).lower())
            .tag("source", "synthetic_generator" if reading.simulated else "pzem004t_v3")
            .field("voltage_v", reading.voltage_v)
            .field("current_a", reading.current_a)
            .field("power_w", reading.power_w)
            .field("energy_kwh", reading.energy_kwh)
            .field("power_factor", reading.power_factor)
            .field("frequency_hz", reading.frequency_hz)
            .field("valid", reading.valid)
            .time(timestamp, WritePrecision.S)
        )
        self.write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)

    def process_energy_cost(self, reading: RoomReading, timestamp: datetime) -> tuple[float, float]:
        """Integrate room power over sample interval and estimate tariff cost."""
        previous = self.last_room_sample.get(reading.room_id)
        self.last_room_sample[reading.room_id] = (timestamp, reading.power_w)
        if previous is None:
            return 0.0, 0.0
        previous_time, _previous_power = previous
        elapsed_seconds = (timestamp - previous_time).total_seconds()
        if elapsed_seconds <= 0 or elapsed_seconds > 300:
            return 0.0, 0.0  # Ignore first sample and long telemetry outages.
        kwh_delta = (reading.power_w / 1000.0) * (elapsed_seconds / 3600.0)
        if kwh_delta <= 0:
            return 0.0, 0.0

        self.reset_month_if_needed(timestamp)
        cost = calculate_incremental_cost(
            kwh_delta, timestamp, self.month_to_date_kwh, TARIFF_PROFILE
        )
        self.month_to_date_kwh += kwh_delta
        self.month_cost_vnd += cost

        energy_point = (
            Point("bems_energy")
            .tag("room_id", str(reading.room_id))
            .tag("simulated", str(reading.simulated).lower())
            .field("energy_delta_kwh", kwh_delta)
            .field("estimated_cost_vnd", cost)
            .field("hybrid_demo_month_to_date_kwh", self.month_to_date_kwh)
            .field("hybrid_demo_month_to_date_cost_vnd", self.month_cost_vnd)
            .time(timestamp, WritePrecision.S)
        )
        self.write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=energy_point)
        return kwh_delta, cost

    def publish_alert(
        self, room_id: int, code: str, detail: str, timestamp: datetime,
        power_w: float, simulated: bool,
    ) -> None:
        key = (room_id, code)
        now = time.time()
        if now - self.last_alert_at.get(key, 0.0) < ANOMALY_ALERT_COOLDOWN_SECONDS:
            return
        self.last_alert_at[key] = now

        alert = {
            "timestamp": timestamp.isoformat(),
            "room_id": room_id,
            "severity": "warning",
            "code": code,
            "detail": detail,
            "power_w": round(power_w, 2),
            "simulated": bool(simulated),
            "source": "synthetic_generator" if simulated else "pzem004t_v3",
            "diagnostic_scope": "energy_behavior_only_not_electrical_protection",
        }
        self.mqtt_client.publish(MQTT_ALERT_TOPIC, json.dumps(alert), qos=0, retain=False)
        alert_point = (
            Point("bems_alerts")
            .tag("room_id", str(room_id))
            .tag("code", code)
            .tag("simulated", str(simulated).lower())
            .field("severity", "warning")
            .field("detail", detail)
            .field("power_w", float(power_w))
            .time(timestamp, WritePrecision.S)
        )
        self.write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=alert_point)
        LOGGER.warning("ROOM %02d ALERT [%s] simulated=%s: %s (%.1f W)",
                       room_id, code, simulated, detail, power_w)

    def maybe_run_demand_response(self, building_power_w: float, rooms: list[RoomReading], timestamp: datetime) -> None:
        """Publish simulated load-shed requests during the configured peak period."""
        if not DR_ENABLED or not DRY_RUN:
            # The supplied demo intentionally refuses non-dry-run operation.
            return
        if classify_time_period(timestamp) != "peak":
            return
        if building_power_w < DR_BUILDING_LIMIT_W:
            return

        now = time.time()
        # Room 01 is the only physical meter. DR can nominate virtual rooms only.
        candidates = [r for r in rooms if r.simulated and r.room_id in DR_SHEDTABLE_ROOM_IDS]
        candidates.sort(key=lambda item: item.power_w, reverse=True)
        for reading in candidates[:2]:
            if now - self.last_dr_at.get(reading.room_id, 0.0) < DR_COMMAND_COOLDOWN_SECONDS:
                continue
            self.last_dr_at[reading.room_id] = now
            duration_hours = DR_ESTIMATE_MINUTES / 60.0
            # The ESP32 virtual-room model keeps 25% load by default; ROI therefore
            # estimates only the configured reduced share, not the full baseline.
            saved_fraction = min(max(DR_VIRTUAL_REDUCTION_PERCENT / 100.0, 0.0), 1.0)
            estimated_kwh = (reading.power_w / 1000.0) * duration_hours * saved_fraction
            unit_price = tariff_price_vnd_per_kwh(
                timestamp, TARIFF_PROFILE, self.month_to_date_kwh
            )
            estimated_saving_vnd = estimated_kwh * unit_price * (1.0 + VAT_RATE)
            command = {
                "action": "SIMULATE_SHED_REQUEST",
                "room_id": reading.room_id,
                "duration_minutes": DR_ESTIMATE_MINUTES,
                "virtual_reduction_percent": DR_VIRTUAL_REDUCTION_PERCENT,
                "dry_run": True,
                "estimated_saving_kwh": round(estimated_kwh, 5),
                "estimated_saving_vnd": round(estimated_saving_vnd, 2),
                "timestamp": timestamp.isoformat(),
                "note": "Software demo only; the ESP32 changes virtual-room data only.",
            }
            topic = f"{MQTT_COMMAND_PREFIX}/{reading.room_id}"
            self.mqtt_client.publish(topic, json.dumps(command), qos=0, retain=False)

            roi_point = (
                Point("bems_roi")
                .tag("room_id", str(reading.room_id))
                .field("estimated_saving_kwh", estimated_kwh)
                .field("estimated_saving_vnd", estimated_saving_vnd)
                .field("dry_run", True)
                .time(timestamp, WritePrecision.S)
            )
            self.write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=roi_point)
            LOGGER.info("DR dry-run request: room=%d estimated saving=%.2f VND",
                        reading.room_id, estimated_saving_vnd)

    def start_lstm_training_if_due(self) -> None:
        """Run TensorFlow training outside the MQTT receive callback thread."""
        if time.time() - self.last_lstm_attempt < LSTM_RETRAIN_SECONDS:
            return
        with self.lstm_lock:
            if self.lstm_training:
                return
            self.lstm_training = True
            self.last_lstm_attempt = time.time()

        def training_worker() -> None:
            try:
                result = self.ai.train_and_forecast_lstm(self.query_api, self.write_api)
                if result:
                    LOGGER.info("Background LSTM job finished: %s", result)
            except Exception:
                LOGGER.exception("Background LSTM training or forecast failed")
            finally:
                with self.lstm_lock:
                    self.lstm_training = False

        threading.Thread(
            target=training_worker,
            name="bems-lstm-training",
            daemon=True,
        ).start()

    def process_telemetry(self, payload: dict[str, Any]) -> None:
        """Validate and store 1 physical PZEM row plus 19 labelled virtual rows."""
        if not isinstance(payload, dict) or not isinstance(payload.get("rooms"), list):
            raise ValueError("Payload must be a JSON object containing a rooms array")

        timestamp = parse_timestamp(payload)
        readings = [parse_room(item) for item in payload["rooms"]]
        if len(readings) != 20:
            raise ValueError(f"Expected 20 rooms (1 physical + 19 simulated); received {len(readings)}")
        ids = [item.room_id for item in readings]
        if len(set(ids)) != 20 or set(ids) != set(range(1, 21)):
            raise ValueError("Payload must contain each room ID exactly once (1..20)")

        # Enforce the source contract: only Room 01 may be physical, and all
        # Rooms 02..20 must be synthetic. Never let a payload silently relabel
        # virtual data as measured hardware data (or vice versa).
        by_id = {item.room_id: item for item in readings}
        if by_id[1].simulated:
            raise ValueError("Room 01 must have simulated=false (physical PZEM)")
        incorrectly_physical = [room_id for room_id in range(2, 21)
                                if not by_id[room_id].simulated]
        if incorrectly_physical:
            raise ValueError(
                "Rooms 02..20 must have simulated=true; invalid IDs: "
                + ",".join(map(str, incorrectly_physical))
            )

        physical_power_w = 0.0
        simulated_power_w = 0.0
        valid_physical_count = 0
        valid_simulated_count = 0

        for reading in readings:
            # Invalid Room 01 readings are skipped instead of being replaced by
            # invented numbers. All virtual-room samples remain explicitly tagged.
            if not reading.valid:
                continue

            self.write_room_measurement(reading, timestamp)
            self.process_energy_cost(reading, timestamp)

            if reading.simulated:
                simulated_power_w += reading.power_w
                valid_simulated_count += 1
            else:
                physical_power_w += reading.power_w
                valid_physical_count += 1

            is_anomaly, reason = self.ai.observe_room(reading, timestamp)
            if is_anomaly:
                self.publish_alert(
                    reading.room_id, reason, reason, timestamp,
                    reading.power_w, reading.simulated,
                )

        hybrid_power_w = physical_power_w + simulated_power_w
        building_point = (
            Point("bems_building")
            .field("physical_room01_power_w", physical_power_w)
            .field("simulated_rooms_power_w", simulated_power_w)
            .field("hybrid_demo_total_power_w", hybrid_power_w)
            .field("room_count", 20)
            .field("physical_room_count_valid", valid_physical_count)
            .field("simulated_room_count_valid", valid_simulated_count)
            .field("hybrid_demo_monthly_energy_kwh", self.month_to_date_kwh)
            .field("hybrid_demo_monthly_cost_vnd", self.month_cost_vnd)
            .field("is_hybrid_demo", True)
            .time(timestamp, WritePrecision.S)
        )
        self.write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=building_point)

        # DR operates only on virtual room values and is permanently dry-run.
        self.maybe_run_demand_response(hybrid_power_w, readings, timestamp)
        self.ai.train_anomaly_models_if_due()
        self.start_lstm_training_if_due()

        LOGGER.info(
            "Stored %d room rows | PZEM valid=%d | simulated valid=%d | "
            "hybrid-demo power=%.1f W | demo monthly=%.4f kWh / %.0f VND | tariff=%s",
            len(readings), valid_physical_count, valid_simulated_count,
            hybrid_power_w, self.month_to_date_kwh, self.month_cost_vnd, TARIFF_PROFILE,
        )

    def run(self) -> None:
        """Start MQTT loop and keep the service alive with reconnect handling."""
        self.mqtt_client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        self.mqtt_client.loop_start()
        LOGGER.info("BEMS AI service started; tariff profile=%s; DRY_RUN=%s",
                    TARIFF_PROFILE, DRY_RUN)
        LOGGER.info("TOU schedule: %s", LOCAL_TIME_PERIODS)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            LOGGER.info("Stopping BEMS AI service...")
        finally:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            self.write_api.close()
            self.influx.close()


def main() -> None:
    """Validate critical configuration and launch the backend."""
    if not DRY_RUN:
        raise RuntimeError("Safety lock: this supplied build requires DRY_RUN=true")
    if TARIFF_PROFILE not in TARIFF_PROFILES:
        raise ValueError(
            f"Unsupported tariff profile {TARIFF_PROFILE!r}. "
            f"Choose one of: {', '.join(TARIFF_PROFILES)}"
        )
    if not INFLUX_TOKEN or INFLUX_TOKEN == "replace-this-token":
        LOGGER.warning("INFLUX_TOKEN is still the template value; check your .env file.")
    service = BemsService()
    service.run()


if __name__ == "__main__":
    main()
