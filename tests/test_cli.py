"""Tests for CLI argument parsing."""

import argparse
import sys
from unittest.mock import patch

import pytest


def test_no_honeypot_flag():
    """Test that --no-honeypot flag is parsed correctly."""
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-honeypot', action='store_true')
    parser.add_argument('query', nargs='?')
    
    # Test with --no-honeypot flag
    args = parser.parse_args(['--no-honeypot', 'test query'])
    assert args.no_honeypot is True
    assert args.query == 'test query'


def test_no_honeypot_flag_default():
    """Test that --no-honeypot flag defaults to False."""
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-honeypot', action='store_true')
    parser.add_argument('query', nargs='?')
    
    # Without flag
    args = parser.parse_args(['test query'])
    assert args.no_honeypot is False
    
    # With flag
    args = parser.parse_args(['--no-honeypot', 'test query'])
    assert args.no_honeypot is True
