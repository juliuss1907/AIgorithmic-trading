"""Two separate phases enforce threshold locking before test outcomes are replayed."""
import argparse
from datetime import datetime
from pathlib import Path

from intraday.jev_review_evaluation import evaluate

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('phase', choices=('tune','test'))
for name in ('database','funding-dir','cache','approved-plan','lock','report-dir','output'):
    parser.add_argument('--'+name, type=Path, required=True)
parser.add_argument('--end', type=datetime.fromisoformat, required=True)
parser.add_argument('--tuning-receipt', type=Path, help='completed tuning receipt with frozen base-rate')
parser.add_argument('--expected-tuning-hash', help='SHA256 of the pre-test tuning receipt')
args = parser.parse_args()
if args.phase == 'test' and (args.tuning_receipt is None or not args.expected_tuning_hash):
    parser.error('test requires the completed tuning receipt and its SHA256')
evaluate(args)
