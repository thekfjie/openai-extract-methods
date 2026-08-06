package extractmethods

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

var ErrAccountActive = errors.New("同一账号已有任务正在运行")

type accountRunLock struct {
	file *os.File
}

type accountRunOwner struct {
	Service   string `json:"service"`
	JobID     string `json:"jobId"`
	Method    string `json:"method"`
	Label     string `json:"label,omitempty"`
	StartedAt string `json:"startedAt"`
}

func credentialAccountIdentity(credential Credential) string {
	if accountID := strings.TrimSpace(credential.AccountID); accountID != "" {
		return "account:" + accountID
	}
	if accountID := accountIDFromJWT(credential.AccessToken); accountID != "" {
		return "account:" + accountID
	}
	if email := strings.ToLower(strings.TrimSpace(credential.Email)); email != "" {
		return "email:" + email
	}
	return "token:" + tokenHash(credential.AccessToken)
}

func acquireAccountRunLocks(root string, credentials []Credential, owner accountRunOwner) ([]*accountRunLock, error) {
	if strings.TrimSpace(root) == "" {
		return nil, nil
	}
	if err := os.MkdirAll(root, 0o700); err != nil {
		return nil, fmt.Errorf("创建账号运行锁目录失败: %w", err)
	}
	locks := make([]*accountRunLock, 0, len(credentials))
	seen := make(map[string]bool, len(credentials))
	for _, credential := range credentials {
		identity := credentialAccountIdentity(credential)
		if seen[identity] {
			releaseAccountRunLocks(locks)
			return nil, fmt.Errorf("%w：提交内容中账号 %s 重复", ErrAccountActive, firstNonEmpty(credential.Email, credential.Label, "未知账号"))
		}
		seen[identity] = true
		name := tokenHash(identity) + ".lock"
		file, err := os.OpenFile(filepath.Join(root, name), os.O_CREATE|os.O_RDWR, 0o600)
		if err != nil {
			releaseAccountRunLocks(locks)
			return nil, fmt.Errorf("打开账号运行锁失败: %w", err)
		}
		if err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
			detail := readAccountRunOwner(file)
			_ = file.Close()
			releaseAccountRunLocks(locks)
			label := firstNonEmpty(credential.Email, credential.Label, "未知账号")
			if detail.Service != "" {
				return nil, fmt.Errorf("%w：%s 正在 %s/%s 中运行，请等待该任务结束或先停止它", ErrAccountActive, label, detail.Service, detail.Method)
			}
			return nil, fmt.Errorf("%w：%s 正在另一项提炼流程中运行，请等待该任务结束或先停止它", ErrAccountActive, label)
		}
		owner.Label = firstNonEmpty(credential.Email, credential.Label)
		owner.StartedAt = time.Now().UTC().Format(time.RFC3339)
		encoded, _ := json.Marshal(owner)
		if err := file.Truncate(0); err == nil {
			_, _ = file.WriteAt(encoded, 0)
			_ = file.Sync()
		}
		locks = append(locks, &accountRunLock{file: file})
	}
	return locks, nil
}

func readAccountRunOwner(file *os.File) accountRunOwner {
	var owner accountRunOwner
	buffer := make([]byte, 4096)
	count, _ := file.ReadAt(buffer, 0)
	_ = json.Unmarshal(buffer[:count], &owner)
	return owner
}

func releaseAccountRunLocks(locks []*accountRunLock) {
	for _, lock := range locks {
		if lock == nil || lock.file == nil {
			continue
		}
		_ = syscall.Flock(int(lock.file.Fd()), syscall.LOCK_UN)
		_ = lock.file.Close()
	}
}
