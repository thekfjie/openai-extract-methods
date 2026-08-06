package fingerprintsdk

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestCloudArgumentsAreOptIn(t *testing.T) {
	arguments, err := (Runner{}).cloudArguments("")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(arguments, []string{"generate"}) {
		t.Fatalf("unexpected local arguments: %#v", arguments)
	}
}

func TestCloudArgumentsUseProtectedHeadersFile(t *testing.T) {
	headersFile := filepath.Join(t.TempDir(), "headers.json")
	if err := os.WriteFile(headersFile, []byte(`{"token":"test"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	runner := Runner{
		CloudEnabled: true, CloudBaseURL: "https://fingerprint.example.test/api",
		CloudHeadersFile: headersFile, CloudOmitMAC: true,
	}
	arguments, err := runner.cloudArguments("cloud")
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		"generate-cloud", "--base-url", "https://fingerprint.example.test/api",
		"--headers-file", headersFile, "--no-cloud-mac",
	}
	if !reflect.DeepEqual(arguments, want) {
		t.Fatalf("unexpected cloud arguments: %#v", arguments)
	}
}

func TestCloudHeadersMustBePrivate(t *testing.T) {
	headersFile := filepath.Join(t.TempDir(), "headers.json")
	if err := os.WriteFile(headersFile, []byte(`{}`), 0o644); err != nil {
		t.Fatal(err)
	}
	_, err := (Runner{
		CloudEnabled: true, CloudBaseURL: "https://fingerprint.example.test/api", CloudHeadersFile: headersFile,
	}).cloudArguments("cloud")
	if err == nil {
		t.Fatal("expected permissions error")
	}
}

func TestRequestSourceOverridesConfiguredMode(t *testing.T) {
	arguments, err := (Runner{CloudEnabled: true}).cloudArguments("local")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(arguments, []string{"generate"}) {
		t.Fatalf("local override used cloud arguments: %#v", arguments)
	}
}
