# Azure Durable Function for Document Comparison

This repository contains an Azure Durable Function application for document comparison and processing.

## New API Endpoints

### 1. List PDFs with Matching JSON Files

This API scans the `cdp-ext` blob storage container and returns a list of PDF files that have matching JSON files.

**Endpoint:** `GET /api/get-document-list`

**Response:**

```json
{
  "files": ["document1.pdf", "document2.pdf", ...]
}
```

**Error Response:**

```json
{
  "error": "Error message"
}
```

### 2. Enhanced Document Comparison API

The existing comparison API has been enhanced to accept a pair of filenames as alternative input. It can now be used in two ways:

#### A. Traditional File Upload Method

**Endpoint:** `POST /api/compare_requirements`

**Form Data:**

- `file1`: First PDF file
- `file2`: Second PDF file
- `file1IsExternal`: Whether the first file is external (1/true/True or 0/false/False)
- `file2IsExternal`: Whether the second file is external (1/true/True or 0/false/False)
- `comparisonMode`: Optional comparison mode parameter

#### B. Filename-Based Method

**Endpoint:** `POST /api/compare_requirements`

**JSON Body:**

```json
{
  "file1Name": "document1.pdf",
  "file2Name": "document2.pdf",
  "file1IsExternal": true,
  "file2IsExternal": true,
  "comparisonMode": "optional_comparisonMode"
}
```

**Notes:**

- When using the filename-based method, the function will look for the specified files in the blob storage.
- `file1IsExternal` and `file2IsExternal` default to `true` if not specified, meaning the files will be looked for in the `cdp-ext` container.
- If set to `false`, the function will look for the file in the `cdp` container.

**Response:**
Both methods return the same response format, with a status indicating that the comparison is running.

**Error Responses:**

- 400: Missing required parameters
- 404: File not found in blob storage
- 500: Server error

## Application Insights Integration

This application now includes Azure Application Insights integration using the Azure Monitor OpenTelemetry SDK. This provides better timing information, distributed tracing, and performance monitoring.

### Setup Application Insights

1. Create an Application Insights resource in the Azure portal
2. Get the connection string from the resource overview page
3. Set the `APPLICATIONINSIGHTS_CONNECTION_STRING` environment variable in your Azure Function app settings:

```bash
APPLICATIONINSIGHTS_CONNECTION_STRING=YOUR_CONNECTION_STRING_HERE
```

### Key Benefits

- **More accurate timing estimates**: Processing time estimates have been adjusted to be more realistic
  - Minimum estimate time set to 60 seconds
  - Multiplier increased from 1.2x to 2.5x
  - Based on actual processing history

- **Distributed tracing**: Track operations across your entire system
  - Traces for file uploads, extraction, and comparison
  - Custom properties for files, token counts, and processing details

- **Live Metrics**: View real-time performance data

- **Custom Events and Metrics**: The integration includes tracking for:
  - PDF extraction success/failure
  - Token counting
  - Time estimation methodology
  - File sizes and processing duration

### Monitoring Your Application

Once set up, you can view detailed telemetry in the Azure portal under your Application Insights resource:

- Go to the **Application Insights** tab within your function app
- View **Live Metrics** for real-time monitoring
- Use **Application Map** to visualize dependencies
- Explore **Performance** and **Failures** to diagnose issues
- Use **Logs** for custom queries and analysis

### Sample Log Queries

Here are some useful Application Insights Kusto queries for monitoring your function app:

#### View all operation timings with details

```kusto
traces
| where message startswith "Completed operation tracking"
| extend operationName = tostring(customDimensions.operationName),
         durationSeconds = todouble(customDimensions.duration_seconds),
         success = tostring(customDimensions.success),
         fileName = tostring(customDimensions.fileName)
| project timestamp, operationName, durationSeconds, success, fileName, customDimensions
| order by timestamp desc
```

#### Track errors in PDF processing

```kusto
traces
| where customDimensions.error != ""
| extend error = tostring(customDimensions.error),
         errorType = tostring(customDimensions.error_type),
         operationName = tostring(customDimensions.operationName)
| project timestamp, operationName, error, errorType, customDimensions
| order by timestamp desc
```

#### Analyze average processing times by operation

```kusto
traces
| where message startswith "Completed operation tracking"
| extend operationName = tostring(customDimensions.operationName),
         durationSeconds = todouble(customDimensions.duration_seconds),
         success = tostring(customDimensions.success)
| where success == "True"
| summarize 
    count(),
    avg(durationSeconds),
    min(durationSeconds),
    max(durationSeconds),
    percentile(durationSeconds, 95)
by operationName
| order by avg_durationSeconds desc
```

## Integration Example

Here's an example of how to integrate these APIs in a frontend application:

```javascript
// List PDFs with matching JSON files
async function getPdfsWithJson() {
  const response = await fetch('/api/get-document-list');
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error || 'Unknown error');
  }
  const data = await response.json();
  return data.files;
}

// Compare documents using filenames
async function compareDocumentsByFilename(file1Name, file2Name) {
  const response = await fetch('/api/compare_requirements', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      file1Name: file1Name,
      file2Name: file2Name,
      file1IsExternal: true,
      file2IsExternal: true
    })
  });
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error || 'Unknown error');
  }
  
  return await response.json();
}
```
