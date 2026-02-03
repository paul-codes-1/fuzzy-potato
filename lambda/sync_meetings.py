#!/usr/bin/env python3
"""
AWS Lambda handler for LFUCG Meeting Pipeline sync.

Triggered by EventBridge schedule (12pm and 8pm weekdays) to:
1. Download existing state from S3
2. Process new meeting clips
3. Update the search index
4. Sync changes back to S3 for frontend serving

Environment Variables Required:
- OPENAI_API_KEY: OpenAI API key for transcription/summarization
- S3_BUCKET: S3 bucket name for input/output files

Optional Environment Variables:
- FIRST_CLIP_ID: Starting clip ID for initial sync (default: 6669)
- S3_DATA_PREFIX: Prefix for data in S3 (default: 'data/')

S3 Structure:
  s3://<bucket>/<prefix>/
    state.json              # Pipeline state (tracks processed clips)
    index.json              # Search index for frontend
    available_clips.json    # Probed clip IDs (optional)
    clips/
      {clip_id}/
        metadata.json
        summary.html
        summary.txt
        transcript_*.txt
        *.mp3 (if audio kept)
        agenda_*.pdf/txt
        minutes_*.pdf/txt
"""

import os
import sys
import json
import boto3
from pathlib import Path
from botocore.exceptions import ClientError

# Add parent directory to path to import main pipeline
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import LFUCGPipeline


def download_from_s3(bucket: str, prefix: str, local_dir: str, files_only: list = None) -> int:
    """
    Download files from S3 to local directory.

    Args:
        bucket: S3 bucket name
        prefix: S3 prefix (e.g., 'data/')
        local_dir: Local directory to download to
        files_only: If provided, only download these specific files (relative to prefix)

    Returns:
        Number of files downloaded
    """
    s3 = boto3.client('s3')
    downloaded = 0
    local_path = Path(local_dir)
    local_path.mkdir(parents=True, exist_ok=True)

    if files_only:
        # Download specific files only
        for rel_path in files_only:
            key = prefix + rel_path
            local_file = local_path / rel_path
            local_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                s3.download_file(bucket, key, str(local_file))
                downloaded += 1
                print(f"Downloaded: {key}")
            except ClientError as e:
                if e.response['Error']['Code'] == '404':
                    print(f"File not found in S3: {key}")
                else:
                    raise
    else:
        # Download all files under prefix
        paginator = s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                rel_path = key[len(prefix):]  # Remove prefix
                if not rel_path:  # Skip the prefix itself
                    continue

                local_file = local_path / rel_path
                local_file.parent.mkdir(parents=True, exist_ok=True)

                s3.download_file(bucket, key, str(local_file))
                downloaded += 1

        print(f"Downloaded {downloaded} files from s3://{bucket}/{prefix}")

    return downloaded


def sync_to_s3(local_dir: str, bucket: str, prefix: str = '', changed_clips: list = None) -> int:
    """
    Sync local directory to S3 bucket.

    Args:
        local_dir: Local directory to upload from
        bucket: S3 bucket name
        prefix: S3 prefix (e.g., 'data/')
        changed_clips: If provided, only upload files for these clip IDs plus root files

    Returns:
        Number of files uploaded
    """
    s3 = boto3.client('s3')
    uploaded = 0

    local_path = Path(local_dir)

    for file_path in local_path.rglob('*'):
        if not file_path.is_file():
            continue

        rel_path = str(file_path.relative_to(local_path))

        # If changed_clips specified, only upload relevant files
        if changed_clips:
            # Always upload root-level files (state.json, index.json, available_clips.json)
            is_root_file = '/' not in rel_path and '\\' not in rel_path
            # Check if file is in a changed clip directory
            is_changed_clip = any(
                rel_path.startswith(f'clips/{clip_id}/') or rel_path.startswith(f'clips\\{clip_id}\\')
                for clip_id in changed_clips
            )
            if not is_root_file and not is_changed_clip:
                continue

        key = prefix + rel_path

        # Determine content type
        content_type = 'application/octet-stream'
        suffix = file_path.suffix.lower()
        if suffix == '.json':
            content_type = 'application/json'
        elif suffix == '.html':
            content_type = 'text/html'
        elif suffix == '.txt':
            content_type = 'text/plain'
        elif suffix == '.mp3':
            content_type = 'audio/mpeg'
        elif suffix == '.pdf':
            content_type = 'application/pdf'

        s3.upload_file(
            str(file_path),
            bucket,
            key,
            ExtraArgs={'ContentType': content_type}
        )
        uploaded += 1

    print(f"Uploaded {uploaded} files to s3://{bucket}/{prefix}")
    return uploaded


def handler(event, context):
    """
    Lambda handler for meeting sync.

    Event can include:
    - max_clips: Maximum clips to process (default: 5)
    - force: Force reprocessing (default: False)
    - full_sync: Download all existing data from S3 (default: False, only downloads state files)
    """
    print(f"Starting meeting sync. Event: {json.dumps(event)}")

    # Configuration
    max_clips = event.get('max_clips', 5)
    force = event.get('force', False)
    full_sync = event.get('full_sync', False)
    output_dir = '/tmp/lfucg_output'
    s3_bucket = os.environ.get('S3_BUCKET')
    s3_prefix = os.environ.get('S3_DATA_PREFIX', 'data/')

    # Ensure prefix ends with /
    if s3_prefix and not s3_prefix.endswith('/'):
        s3_prefix += '/'

    # Verify required env vars
    if not os.environ.get('OPENAI_API_KEY'):
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'OPENAI_API_KEY not set'})
        }

    if not s3_bucket:
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'S3_BUCKET not set'})
        }

    try:
        # Download existing state from S3
        print(f"Downloading state from s3://{s3_bucket}/{s3_prefix}")
        if full_sync:
            # Download everything (useful for debugging or reprocessing)
            download_from_s3(s3_bucket, s3_prefix, output_dir)
        else:
            # Only download state files needed to track progress
            state_files = ['state.json', 'available_clips.json']
            download_from_s3(s3_bucket, s3_prefix, output_dir, files_only=state_files)

        # Initialize pipeline
        pipeline = LFUCGPipeline(
            output_dir=output_dir,
            force_reprocess=force,
            verbose=True
        )

        # Process new clips
        print(f"Auto-processing up to {max_clips} clips...")
        results = pipeline.auto_process(max_clips=max_clips)

        processed_clips = results.get('processed', [])
        failed_clips = results.get('failed', [])
        processed_count = len(processed_clips)
        failed_count = len(failed_clips)

        print(f"Processed: {processed_count}, Failed: {failed_count}")

        # Generate search index if any clips were processed
        if processed_count > 0:
            print("Generating search index...")
            index_path = pipeline.generate_search_index()
            print(f"Index generated: {index_path}")

        # Sync changes to S3
        # Always sync state files; only sync clip data if clips were processed
        print(f"Syncing to s3://{s3_bucket}/{s3_prefix}")
        if processed_count > 0:
            # Upload state files + processed clip directories
            uploaded = sync_to_s3(output_dir, s3_bucket, s3_prefix, changed_clips=processed_clips)
        else:
            # Only upload state files (state.json may have updated last_processed_clip_id)
            uploaded = sync_to_s3(output_dir, s3_bucket, s3_prefix, changed_clips=[])

        return {
            'statusCode': 200,
            'body': json.dumps({
                'processed': processed_clips,
                'failed': failed_clips,
                'last_processed_clip_id': pipeline.state['last_processed_clip_id'],
                'files_uploaded': uploaded
            })
        }

    except Exception as e:
        print(f"Error during sync: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


# For local testing
if __name__ == '__main__':
    # Simulate Lambda invocation
    test_event = {'max_clips': 1}
    test_context = None
    result = handler(test_event, test_context)
    print(json.dumps(result, indent=2))
