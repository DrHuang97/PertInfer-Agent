"""JSON command protocol example. Replace the backend with your model SDK.

This example deliberately accepts only the synthetic demo task. No API call,
model credential, reference graph, benchmark score or historical output is used.
"""
import json
import sys

from pertinfer_agent.demo import ScriptedFixtureModel

request = json.load(sys.stdin)
response = ScriptedFixtureModel().next_action(request)
print(json.dumps(response, ensure_ascii=False))
