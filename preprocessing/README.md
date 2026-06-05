# Preprocessing Pipeline

Data preprocessing and analysis pipeline for the Field 2026 Social project.

## Structure

### [security_camera/](security_camera/)
- Copy and storage protocols for security camera recordings
- Raw and processed data organization

### [lfp_recording/](lfp_recording/)
- Copy and storage protocols for LFP recordings (to be added)
- Raw and processed data organization

### [data_merging/](data_merging/)
- [merge_cameras.py](data_merging/merge_cameras.py): Merge recordings from multiple cameras
- Temporal alignment and synchronization

### [computer_vision/](computer_vision/)
- [animal_tracking.py](computer_vision/animal_tracking.py): Computer vision for animal position tracking
- Trajectory extraction and visualization

## Workflow

1. **Data Collection**: Follow copy and storage protocols for each data type
2. **Data Merging**: Synchronize and merge multi-camera recordings
3. **Position Tracking**: Extract animal positions using computer vision
4. **Analysis**: Process synchronized behavioral and neural data

## Getting Started

See individual protocol documents and module docstrings for detailed usage instructions.
