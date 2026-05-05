// gen_canon.go — generates ground-truth canonical Dec output for cross-language test.
//
// Builds a JSON list of {input, output} pairs by parsing each input with
// allora-chain's math.Dec (which wraps cockroachdb/apd v3) and calling
// Marshal() — the exact path the chain uses for signature verification.
//
// To regenerate (requires Go and the allora-chain repo as a sibling):
//
//	cd tests/canon && go run gen_canon.go > canon_ground_truth.json
package main

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/allora-network/allora-chain/math"
)

type pair struct {
	Input  string `json:"input"`
	Output string `json:"output"`
}

// Inputs the chain might receive. Each is parsed via math.NewDecFromString
// and re-emitted via Dec.Marshal() — the canonical form that the chain
// hashes for signature verification.
var inputs = []string{
	// === whole numbers ===
	"0", "1", "10", "100", "1000", "10000",
	"123", "12345", "1234567",
	"1234567890123456789",

	// === simple positive decimals ===
	"0.1", "0.01", "0.001", "0.0001", "0.00001", "0.000001",
	"0.0000001", "0.00000001", "0.000000001",
	"1.5", "1.50", "1.500", "1.5000",
	"10.5", "10.50",
	"0.10", "0.100", "0.1000",
	"123.456", "1.2345", "1.23456789",

	// === scientific notation, lowercase e ===
	"1e0", "1e1", "1e2", "1e10", "1e100",
	"1e-1", "1e-2", "1e-6", "1e-7", "1e-8", "1e-100",
	"1.2345e-7", "1.2345e-6", "1.2345e7",
	"9.9999e-7", "1.000001e-7",

	// === scientific notation, uppercase E and with explicit signs ===
	"1.2345E-7", "1.2345E+7", "1.2345E7",
	"1E+10", "1E-10",

	// === precision preservation across re-marshal (Florian called these
	// "fucking weird" — apd preserves coefficient digits, so each round-trips
	// to itself with different precision in the same E-form) ===
	"1E-7", "1.0E-7", "1.00E-7", "1.000E-7",
	"1E+7", "1.0E+7", "1.00E+7",

	// === fmtF / fmtE boundary (adjExponentLimit = -6) ===
	"1e-6",          // adj=-6, fmtF
	"1e-7",          // adj=-7, fmtE
	"9.999999e-1",   // adj=-1, fmtF
	"9.999999e-7",   // adj=-7, fmtE
	"1.234567",      // adj=0,  fmtF
	"1.234567e-6",   // adj=-6, fmtF
	"1.234567e-7",   // adj=-7, fmtE

	// === negatives ===
	"-1", "-10", "-100",
	"-1.5", "-1.50",
	"-0.1", "-0.001",
	"-1.2345e-7", "-1.2345e+10",

	// === zero variants ===
	"0", "0.0", "0.00", "0.000",
	"0e0", "0e1", "0e-1", "0e-7", "0e5", "0e10",
	"-0", "-0.0", "-0.000",

	// === explicit plus signs ===
	"+1", "+0.5", "+1.5", "+1.2345e-7",

	// === leading zeros in input (apd normalizes) ===
	"01", "001", "0001.5", "01.0",

	// === high precision ===
	"0.123456789012345678901234567890",
	"1.234567890123456789012345678e-7",
	"1.234567890123456789012345678",

	// === floats that Python's str() produces ===
	"0.1",         // float 0.1
	"1e-05",       // float 1e-5 → str → "1e-05"
	"1.2345e-07",  // float 1.2345e-7 → str → "1.2345e-07"
	"1e+20",       // large float
	"100.0",       // float 100.0
	"-0.0",        // negative zero float

	// === extreme magnitudes ===
	"1e308", "1e-308",
	"1e1000", "1e-1000",

	// === values with embedded zeros ===
	"1.005", "1.0005e-7", "100.001",
	"1000000000000",
	"0.000000000000001",
}

func main() {
	pairs := make([]pair, 0, len(inputs))
	for _, in := range inputs {
		d, err := math.NewDecFromString(in)
		if err != nil {
			fmt.Fprintf(os.Stderr, "skip %q: parse error: %v\n", in, err)
			continue
		}
		bz, err := d.Marshal()
		if err != nil {
			fmt.Fprintf(os.Stderr, "skip %q: marshal error: %v\n", in, err)
			continue
		}
		pairs = append(pairs, pair{Input: in, Output: string(bz)})
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(pairs); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
