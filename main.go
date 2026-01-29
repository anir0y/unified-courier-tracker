package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"io/ioutil"
	"net/http"
	"os"
	"os/exec"
	"regexp"
	"runtime"
	"time"
)

const storageFile = "tracking_list_v2.json"

var BuildAuthor = "Animesh Roy (social/@anir0y)"
var BuildTime = "2026-01-29T14:00:58.806Z"

type Scan struct {
	Location string `json:"location,omitempty"`
	Details  string `json:"details,omitempty"`
	Date     string `json:"date,omitempty"`
	Time     string `json:"time,omitempty"`
}

type Result struct {
	Status          string            `json:"status,omitempty"`
	DeliveryDetails map[string]string `json:"delivery_details,omitempty"`
	Scans           []Scan            `json:"scans,omitempty"`
	Courier         string            `json:"courier,omitempty"`
	TrackingNumber  string            `json:"tracking_number,omitempty"`
	Error           string            `json:"error,omitempty"`
}

func httpClient() *http.Client {
	return &http.Client{Timeout: 15 * time.Second}
}

func fetchBlueDart(id string) Result {
	url := fmt.Sprintf("https://www.bluedart.com/trackdartresultthirdparty?trackFor=0&trackNo=%s", id)
	req, _ := http.NewRequest("GET", url, nil)
	req.Header.Set("User-Agent", "Go-Tracker/1.0")
	resp, err := httpClient().Do(req)
	if err != nil {
		return Result{Error: err.Error(), Courier: "Blue Dart", TrackingNumber: id}
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	h := string(b)
	// Try to extract status from HTML: label "Status" then following <p>
	re := regexp.MustCompile(`(?is)<label[^>]*>\s*Status\s*</label>.*?<p[^>]*>(.*?)</p>`)
	m := re.FindStringSubmatch(h)
	status := "Unknown"
	if len(m) > 1 {
		status = regexp.MustCompile(`\s+`).ReplaceAllString(m[1], " ")
		status = bytes.NewBufferString(status).String()
	}
	return Result{Status: status, Courier: "Blue Dart", TrackingNumber: id, DeliveryDetails: map[string]string{"raw_html_length": fmt.Sprintf("%d", len(h))}}
}

func fetchDTDC(id string) Result {
	url := "https://www.dtdc.com/wp-json/custom/v1/domestic/track"
	payload := map[string]string{"trackType": "cnno", "trackNumber": id}
	data, _ := json.Marshal(payload)
	req, _ := http.NewRequest("POST", url, bytes.NewReader(data))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "Go-Tracker/1.0")
	resp, err := httpClient().Do(req)
	if err != nil {
		return Result{Error: err.Error(), Courier: "DTDC", TrackingNumber: id}
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	var parsed map[string]interface{}
	if err := json.Unmarshal(b, &parsed); err != nil {
		return Result{Error: err.Error(), Courier: "DTDC", TrackingNumber: id}
	}
	res := Result{Courier: "DTDC", TrackingNumber: id, DeliveryDetails: map[string]string{}}
	// Try to extract statuses array
	if statuses, ok := parsed["statuses"].([]interface{}); ok && len(statuses) > 0 {
		var scans []Scan
		for _, si := range statuses {
			if s, ok := si.(map[string]interface{}); ok {
				desc := ""
				if d, ok := s["statusDescription"].(string); ok {
					desc = regexp.MustCompile(`<[^>]+>`).ReplaceAllString(d, " ")
				}
				scans = append(scans, Scan{Location: strVal(s["actCityName"], s["actBranchName"]), Details: desc, Date: firstWord(s["statusTimestamp"]), Time: secondWord(s["statusTimestamp"])})
			}
		}
		res.Scans = scans
		if len(scans) > 0 {
			res.Status = scans[0].Details
		}
	}
	if header, ok := parsed["header"].(map[string]interface{}); ok {
		res.DeliveryDetails["Origin"] = fmt.Sprint(header["originCity"])
		res.DeliveryDetails["Destination"] = fmt.Sprint(header["destinationCity"])
		if cs, ok := header["currentStatusDescription"].(string); ok && cs != "" {
			res.Status = cs
		}
	}
	return res
}

func fetchDelhivery(id string) Result {
	url := fmt.Sprintf("https://dlv-api.delhivery.com/v3/unified-tracking?wbn=%s", id)
	req, _ := http.NewRequest("GET", url, nil)
	req.Header.Set("User-Agent", "Go-Tracker/1.0")
	resp, err := httpClient().Do(req)
	if err != nil {
		return Result{Error: err.Error(), Courier: "Delhivery", TrackingNumber: id}
	}
	defer resp.Body.Close()
	b, _ := ioutil.ReadAll(resp.Body)
	var parsed map[string]interface{}
	if err := json.Unmarshal(b, &parsed); err != nil {
		return Result{Error: err.Error(), Courier: "Delhivery", TrackingNumber: id}
	}
	res := Result{Courier: "Delhivery", TrackingNumber: id, DeliveryDetails: map[string]string{}}
	if dataArr, ok := parsed["data"].([]interface{}); ok && len(dataArr) > 0 {
		if shipment, ok := dataArr[0].(map[string]interface{}); ok {
			if tsArr, ok := shipment["trackingStates"].([]interface{}); ok {
				var scans []Scan
				for _, ts := range tsArr {
					if m, ok := ts.(map[string]interface{}); ok {
						if scArr, ok := m["scans"].([]interface{}); ok {
							for _, s := range scArr {
								if si, ok := s.(map[string]interface{}); ok {
									scans = append(scans, Scan{Location: fmt.Sprint(si["scannedLocation"]), Details: fmt.Sprint(si["scanNslRemark"], si["scan"]), Date: splitDate(fmt.Sprint(si["scanDateTime"])), Time: splitTime(fmt.Sprint(si["scanDateTime"]))})
								}
							}
						}
					}
				}
				res.Scans = scans
				if st, ok := shipment["status"].(map[string]interface{}); ok {
					res.Status = fmt.Sprint(st["status"])
				}
				res.DeliveryDetails["Origin"] = fmt.Sprint(shipment["consignor"])
				res.DeliveryDetails["Destination"] = fmt.Sprint(shipment["destination"])
				res.DeliveryDetails["ExpectedDelivery"] = fmt.Sprint(shipment["deliveryDate"])
			}
		}
	}
	return res
}

func strVal(vals ...interface{}) string {
	for _, v := range vals {
		if s, ok := v.(string); ok && s != "" {
			return s
		}
	}
	return "N/A"
}

func firstWord(v interface{}) string {
	s := fmt.Sprint(v)
	if s == "" {
		return ""
	}
	parts := regexp.MustCompile(`\\s+`).Split(s, -1)
	if len(parts) > 0 {
		return parts[0]
	}
	return ""
}

func secondWord(v interface{}) string {
	s := fmt.Sprint(v)
	parts := regexp.MustCompile(`\\s+`).Split(s, -1)
	if len(parts) > 1 {
		return parts[1]
	}
	return ""
}

func splitDate(s string) string {
	if s == "" {
		return ""
	}
	if idx := regexp.MustCompile(`T`).FindStringIndex(s); idx != nil {
		return s[:idx[0]]
	}
	return s
}

func splitTime(s string) string {
	if s == "" {
		return ""
	}
	if idx := regexp.MustCompile(`T`).FindStringIndex(s); idx != nil {
		rest := s[idx[1]:]
		if len(rest) >= 5 {
			return rest[:5]
		}
		return rest
	}
	return ""
}

func getTracker(courier string, id string) Result {
	switch courier {
	case "Blue Dart":
		return fetchBlueDart(id)
	case "DTDC":
		return fetchDTDC(id)
	case "Delhivery":
		return fetchDelhivery(id)
	default:
		return Result{Error: "Unknown courier", Courier: courier, TrackingNumber: id}
	}
}

func loadStorage() map[string]map[string]interface{} {
	m := map[string]map[string]interface{}{}
	if _, err := os.Stat(storageFile); os.IsNotExist(err) {
		return m
	}
	b, err := ioutil.ReadFile(storageFile)
	if err != nil {
		return m
	}
	json.Unmarshal(b, &m)
	return m
}

func saveStorage(m map[string]map[string]interface{}) error {
	b, _ := json.MarshalIndent(m, "", "  ")
	return ioutil.WriteFile(storageFile, b, 0644)
}

func main() {
	add := flag.String("add", "", "Add ID")
	del := flag.String("delete", "", "Delete ID")
	courier := flag.String("courier", "Blue Dart", "Courier name: \"Blue Dart\", \"DTDC\", \"Delhivery\"")
	jsonOut := flag.Bool("json", false, "Output JSON")
	track := flag.String("tracking_number", "", "Optional tracking number")
	list := flag.Bool("list", false, "List stored IDs")
	version := flag.Bool("version", false, "Print version")
	flag.Parse()

	storage := loadStorage()

	if *version {
		fmt.Printf("Author: %s\nBuildTime: %s\n", BuildAuthor, BuildTime)
		return
	}

	if *add != "" {
		if _, ok := storage[*add]; !ok {
			storage[*add] = map[string]interface{}{"courier": *courier, "status": "Pending"}
			saveStorage(storage)
			fmt.Printf("Added %s (%s)\n", *add, *courier)
		} else {
			fmt.Printf("Tracking number %s already exists.\n", *add)
		}
		return
	}

	if *del != "" {
		if _, ok := storage[*del]; ok {
			delete(storage, *del)
			saveStorage(storage)
			fmt.Printf("Deleted %s\n", *del)
		} else {
			fmt.Printf("ID %s not found\n", *del)
		}
		return
	}

	if *list {
		b, _ := json.MarshalIndent(storage, "", "  ")
		fmt.Println(string(b))
		return
	}

	if *track != "" {
		res := getTracker(*courier, *track)
		if *jsonOut {
			b, _ := json.MarshalIndent(res, "", "  ")
			fmt.Println(string(b))
		} else {
			b, _ := json.MarshalIndent(res, "", "  ")
			fmt.Println(string(b))
		}
		return
	}

	// Default: on Windows, try launching bundled Windows TUI binary; else launch Python TUI if available, else print usage
	if runtime.GOOS == "windows" {
		exe := "tmp-bluedart-windows-amd64.exe"
		if _, err := os.Stat(exe); err == nil {
			cmd := exec.Command(exe)
			cmd.Stdin = os.Stdin
			cmd.Stdout = os.Stdout
			cmd.Stderr = os.Stderr
			if err := cmd.Run(); err != nil {
				fmt.Fprintf(os.Stderr, "Error launching Windows TUI: %v\n", err)
			}
			return
		}
	}
	if _, err := os.Stat("track_shipments.py"); err == nil {
		py := "python3"
		if _, err := exec.LookPath(py); err != nil {
			py = "python"
		}
		cmd := exec.Command(py, "track_shipments.py")
		cmd.Stdin = os.Stdin
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		if err := cmd.Run(); err != nil {
			fmt.Fprintf(os.Stderr, "Error launching Python TUI: %v\n", err)
		}
		return
	}
	flag.Usage()
}
