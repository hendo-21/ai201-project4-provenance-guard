"""Flask app skeleton for Provenance Guard."""

import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import db
from scoring import combine_scores, generate_label
from signals import signal_1_llm, signal_2_stylometric

load_dotenv()

app = Flask(__name__)
limiter = Limiter(key_func=lambda: request.json.get("user_id") if request.is_json else get_remote_address(), app=app)

db.init_db()


def parse_request(required_fields):
    """Extracts and validates required fields from a JSON request body."""
    body = request.get_json(silent=True)
    if not body or not all(field in body for field in required_fields):
        return None
    return body


@app.route("/submit", methods=["POST"])
@limiter.limit("10/minute")
def submit():
    body = parse_request(["user_id", "text"])
    if body is None:
        return jsonify({"error": "missing or malformed request body"}), 400

    user_id = body["user_id"]
    text = body["text"]
    word_count = len(text.split())

    s1_score = signal_1_llm(text)
    s2_score = signal_2_stylometric(text)
    confidence = combine_scores(s1_score, s2_score, word_count)
    label = generate_label(confidence)

    submission = {
        "content_id": str(uuid.uuid4()),
        "user_id": user_id,
        "text": text,
        "s1_score": s1_score,
        "s2_score": s2_score,
        "confidence": confidence,
        "label": label,
        "status": "active",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    db.insert_submission(submission)

    return jsonify(
        {
            "content_id": submission["content_id"],
            "confidence": submission["confidence"],
            "label": submission["label"],
        }
    )


@app.route("/appeal", methods=["POST"])
def appeal():
    body = parse_request(["content_id", "user_id", "appeal_text"])
    if body is None:
        return jsonify({"error": "missing or malformed request body"}), 400

    content_id = body["content_id"]
    submission = db.get_submission(content_id)
    if submission is None:
        return jsonify({"error": "content_id not found"}), 404

    appeal_record = {
        "appeal_id": str(uuid.uuid4()),
        "content_id": content_id,
        "user_id": body["user_id"],
        "appeal_text": body["appeal_text"],
        "appeal_status": "pending",
        "appeal_timestamp": datetime.now(timezone.utc).isoformat(),
    }
    db.insert_appeal(appeal_record)
    db.update_submission_status(content_id, "under_review")

    return jsonify(
        {
            "appeal_id": appeal_record["appeal_id"],
            "message": "Your appeal has been received and will be reviewed.",
        }
    )


@app.route("/log", methods=["GET"])
def log():
    return jsonify(db.get_log())


if __name__ == "__main__":
    app.run(port=5001, debug=True)
