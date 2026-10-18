# 🚌 Smart Personnel Service Management and Dynamic Assignment Model
**Seçil Kauçuk A.Ş. - Human Resources Decision Support System**

This repository contains the complete source code and execution scripts for the **Smart Personnel Service Management and Dynamic Assignment Model**, developed as part of the IE297 Summer Internship at Abdullah Gül University (Industrial Engineering Department).

---

## 📌 Project Overview
The primary goal of this project is to automate, optimize, and streamline the personnel shuttle assignment and routing processes within the Human Resources Department at Seçil Kauçuk A.Ş. 

By replacing manual phone calls and spreadsheet-based tracking, this decision support system dynamically assigns blue-collar workers, white-collar employees, and temporary interns to corporate shuttle routes based on exact geographic locations, shifts, vehicle capacities, and corporate priority constraints.

### Key Features
* **Automated & Rule-Based Assignment:** Assigns personnel based on shift schedules, collar classification (Blue/White-collar), and physical vehicle capacities (16, 29, 46 seats).
* **Geocoding & OSRM Routing Engine:** Resolves open residential addresses into spatial coordinates via OpenStreetMap (Nominatim) and calculates the exact road network distance to shuttle corridors using the Open Source Routing Machine (OSRM).
* **Dynamic Capacity & Intern Priority Management:** Automatically frees seats when temporary intern contracts expire or when employees mark themselves as inactive/on leave, preventing "ghost seat" allocations and saving unnecessary shuttle rental costs.
* **Interactive Desktop User Interface (PySide6 / Qt):** Provides HR specialists with real-time route visualization on Leaflet maps, Excel bulk import/validation tools, and live capacity tracking counters.
* **Local Relational Architecture:** Powered by SQLite to ensure fast batch processing (<3.2 seconds for 660+ records) and offline enterprise reliability.

---

## 🛠️ Tech Stack & Dependencies
* **Programming Language:** Python 3.10+
* **GUI Framework:** PySide6 (Qt for Python)
* **Routing & Geocoding:** Open Source Routing Machine (OSRM), OpenStreetMap Nominatim, Leaflet.js
* **Database Engine:** SQLite3
* **Data Processing:** Pandas, NumPy, OpenPyXL

---

## 🚀 Getting Started

### 1. Prerequisites
Ensure you have Python 3.10 or higher installed on your system.

### 2. Installation
Clone the repository and install the required dependencies:

```bash
git clone [https://github.com/yarengorgulu/IE297-SE-K-KAU-UK-Personel-Service-Shuttle-Assignment.git](https://github.com/yarengorgulu/IE297-SE-K-KAU-UK-Personel-Service-Shuttle-Assignment.git)
cd IE297-SE-K-KAU-UK-Personel-Service-Shuttle-Assignment
