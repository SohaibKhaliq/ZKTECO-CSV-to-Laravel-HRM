# ZKTeco CSV to Laravel HRM Attendance Sync

This Python application synchronizes attendance records from ZKTeco devices (via CSV export) to a Laravel HRM system through API calls.

## Features

- ✅ Reads attendance data from ZKTeco CSV exports
- ✅ Processes check-in and check-out records
- ✅ Syncs attendance to Laravel HRM via REST API
- ✅ Automatic retry mechanism with exponential backoff
- ✅ Comprehensive logging and error handling
- ✅ Duplicate detection and prevention
- ✅ Date-based processing with tracking
- ✅ Configurable via environment variables

## Requirements

- Python 3.8 or higher
- ZKTeco attendance device CSV export file
- Access to Laravel HRM API

## Installation

1. Clone the repository:
```bash
git clone https://github.com/SohaibKhaliq/ZKTECO-CSV-to-Laravel-HRM.git
cd ZKTECO-CSV-to-Laravel-HRM
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure environment variables:
```bash
cp .env.example .env
```

4. Edit `.env` file with your configuration:
```env
# ZKTeco Device Configuration
DEVICE_IP=10.5.8.3
DEVICE_PORT=4370

# HR API Configuration
AUTH_MIDDLEWARE=your_auth_middleware_token
HR_API_BASE_URL=https://hrm.zentacode.com/api
CHECK_IN_ENDPOINT=https://hrm.zentacode.com/api/check-in
CHECK_OUT_ENDPOINT=https://hrm.zentacode.com/api/check-out

# CSV File Configuration
CSV_FILE=Timeset Attendance.csv

# API Rate Limiting
DEFAULT_API_DELAY=1.0
MAX_RETRY_ATTEMPTS=3

# Timezone
TIMEZONE=Asia/Karachi
```

## CSV File Format

The application expects a tab-delimited CSV file with the following format:

```
<user_id>\t<timestamp>\t<field>\t<punch>\t<status>\t<other_fields>
```

Where:
- `user_id`: Employee ID (numeric)
- `timestamp`: Format `YYYY-MM-DD HH:MM:SS`
- `punch`: 0 for check-in, 1 for check-out
- `status`: 25 for palm, other values for finger

## Usage

1. Export attendance data from your ZKTeco device to a CSV file
2. Place the CSV file in the same directory as `main.py` (or specify path in `.env`)
3. Run the application:

```bash
python main.py
```

## How It Works

1. **Validation**: Validates CSV file existence and configuration
2. **Data Loading**: Reads all attendance records from CSV
3. **Date Range**: Determines date range between API last sync and latest device record
4. **Processing**: For each date:
   - Filters records for that specific date
   - Processes check-ins first, then check-outs
   - Sends API calls with retry logic
   - Tracks success/failure counts
5. **Logging**: Maintains detailed logs in JSON files:
   - `all_attendance_records.json` - All attendance records
   - `api_call_logs.json` - API call history
   - `processed_dates.json` - Date processing status

## Features in Detail

### Error Handling
- Automatic retry on network errors (configurable max attempts)
- Timeout handling for API calls
- Duplicate detection to prevent re-processing
- Graceful handling of validation errors

### Logging
- Comprehensive JSON logs for all operations
- Success/failure tracking per date
- Detailed error messages with context
- API response logging

### Security
- Credentials stored in `.env` file (not in code)
- `.env` file excluded from git via `.gitignore`
- Request timeout protection
- Input validation

### Smart Processing
- Skips already processed dates
- Handles duplicate records within same date
- Respects API rate limiting via Retry-After headers
- Processes check-ins before check-outs

## Output Files

The application creates several JSON files for tracking:

- `all_attendance_records.json` - Complete record history
- `last_attendance_from_device.json` - Latest device record
- `last_attendance_from_api.json` - Latest API response
- `attendance_by_date.json` - Records grouped by date
- `filtered_attendance_by_date.json` - Filtered records for current processing date
- `api_call_logs.json` - Detailed API call logs
- `processed_dates.json` - Date processing status tracker

## Troubleshooting

### "CSV file not found"
- Ensure the CSV file exists in the specified location
- Check the `CSV_FILE` setting in `.env`

### "AUTH_MIDDLEWARE not configured"
- Set the `AUTH_MIDDLEWARE` in your `.env` file
- This is required for API authentication

### API Call Failures
- Check network connectivity
- Verify API endpoint URLs in `.env`
- Review `api_call_logs.json` for detailed error messages
- Ensure AUTH_MIDDLEWARE token is valid

### Duplicate Records
- The application automatically skips duplicate records
- Check logs for "already marked" or "already exists" messages
- Review `processed_dates.json` for processing status

## Configuration Reference

| Variable | Description | Default |
|----------|-------------|---------|
| `DEVICE_IP` | ZKTeco device IP address | `10.5.8.3` |
| `DEVICE_PORT` | Device port number | `4370` |
| `AUTH_MIDDLEWARE` | API authentication token | *Required* |
| `HR_API_BASE_URL` | Base URL for HR API | `https://hrm.zentacode.com/api` |
| `CSV_FILE` | Path to attendance CSV file | `Timeset Attendance.csv` |
| `TIMEZONE` | Timezone for timestamps | `Asia/Karachi` |
| `DEFAULT_API_DELAY` | Delay between API calls (seconds) | `1.0` |
| `MAX_RETRY_ATTEMPTS` | Maximum retry attempts per API call | `3` |

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is open source and available under the MIT License.

## Support

For issues and questions, please open an issue on GitHub.
