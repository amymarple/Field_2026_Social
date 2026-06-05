# Security Camera Data - Copy and Storage Protocol

## Overview
Protocol for copying and storing security camera recordings for the Field 2026 Social project.

## Data Transfer

### Step 1: Data Collection
- Ensure all security camera recordings are complete
- Verify file integrity before transfer
- Note recording timestamps and camera IDs

### Step 2: Copy Protocol
```bash
# Example rsync command for secure transfer
rsync -avz --progress /source/camera_data/ /destination/preprocessing/security_camera/raw/
```

### Step 3: Verification
- Check file sizes match source
- Verify timestamps are preserved
- **CRITICAL: Verify continuous 24/7 recording with no time gaps**
- Check for temporal continuity between recording segments
- Log any transfer errors or time discontinuities

## Storage Structure
```
preprocessing/
└── security_camera/
    ├── raw/              # Original recordings
    │   ├── YYYY-MM-DD/   # Organized by date
    │   └── camera_id/    # Organized by camera
    ├── processed/        # Preprocessed data
    └── metadata/         # Recording metadata
```

## Backup Protocol
- Maintain redundant copies on separate drives
- Regular integrity checks (weekly)
- Document all data locations

## Temporal Continuity Verification

### Automated Time Gap Detection
```python
# Check for time gaps in recordings
python preprocessing/security_camera/verify_continuity.py --input raw/ --output metadata/continuity_report.txt
```

### Manual Verification Checklist
- [ ] Confirm recordings cover full 24/7 period
- [ ] No gaps between consecutive video files
- [ ] Frame timestamps are monotonically increasing
- [ ] File boundaries align properly (last frame of file N + 1 frame = first frame of file N+1)
- [ ] Camera system clock synchronization verified

### Gap Detection Thresholds
- **Warning**: Gaps > 1 second
- **Error**: Gaps > 5 seconds
- **Critical**: Gaps > 60 seconds or any missing hours

### Troubleshooting Time Gaps
- Check camera system logs for recording interruptions
- Verify storage device was not full during recording
- Confirm camera system remained powered throughout session
- Check network connectivity logs for IP cameras

## Notes
- LFP recording protocol to be added
- Update this protocol as needed for project requirements
- Temporal continuity is essential for accurate behavioral analysis
