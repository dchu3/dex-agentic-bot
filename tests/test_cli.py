"""Tests for CLI argument parsing."""

import argparse


def test_no_honeypot_flag_enabled():
    """Test that --no-honeypot flag is parsed correctly when enabled."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-honeypot', action='store_true')
    parser.add_argument('query', nargs='?')
    
    # Test with --no-honeypot flag
    args = parser.parse_args(['--no-honeypot', 'test query'])
    assert args.no_honeypot is True
    assert args.query == 'test query'


def test_no_honeypot_flag_default():
    """Test that --no-honeypot flag defaults to False."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-honeypot', action='store_true')
    parser.add_argument('query', nargs='?')
    
    # Without flag - should default to False
    args = parser.parse_args(['test query'])
    assert args.no_honeypot is False
    assert args.query == 'test query'
