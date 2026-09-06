"""Test Risk Classifier."""

import pytest
from voxos.core.risk_engine.classifier import CommandRiskClassifier, RiskLevel


def test_command_risk_classifier():
    classifier = CommandRiskClassifier()
    
    # Safe commands
    assert classifier.assess_command("echo hello") == RiskLevel.SAFE
    assert classifier.assess_command("ls -la") == RiskLevel.SAFE
    assert classifier.assess_command("date") == RiskLevel.SAFE
    assert classifier.assess_command("cat file.txt") == RiskLevel.SAFE
    
    # Medium commands
    assert classifier.assess_command("curl http://example.com") == RiskLevel.MEDIUM
    assert classifier.assess_command("wget http://example.com") == RiskLevel.MEDIUM
    assert classifier.assess_command("apt-get install tree") == RiskLevel.MEDIUM
    
    # High commands
    assert classifier.assess_command("rm -rf /") == RiskLevel.HIGH
    assert classifier.assess_command("sudo rm /root/file") == RiskLevel.HIGH
    assert classifier.assess_command("mv ~ /dev/null") == RiskLevel.HIGH
    assert classifier.assess_command("mkfs.ext4 /dev/sda1") == RiskLevel.HIGH
    assert classifier.assess_command("chmod 777 /etc/passwd") == RiskLevel.HIGH
    assert classifier.assess_command("chown root:root /tmp/exploit") == RiskLevel.HIGH

    # Obfuscated high commands
    assert classifier.assess_command("r\\m -rf /") == RiskLevel.HIGH
    assert classifier.assess_command("s\\u\\d\\o su") == RiskLevel.HIGH
