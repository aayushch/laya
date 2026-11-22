// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

use std::io::{self, Read, Write};
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::os::windows::process::CommandExt;
use std::process::{Child, Command, Stdio};
use std::ptr::{null, null_mut};
use std::time::Duration;
use windows_sys::Win32::Foundation::{
    GetLastError, ERROR_ALREADY_EXISTS, ERROR_FILE_NOT_FOUND, ERROR_INVALID_PARAMETER,
    INVALID_HANDLE_VALUE, WAIT_OBJECT_0, WAIT_TIMEOUT,
};
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, IsProcessInJob,
    JobObjectBasicAccountingInformation, JobObjectExtendedLimitInformation, OpenJobObjectW,
    QueryInformationJobObject, SetInformationJobObject, TerminateJobObject,
    JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows_sys::Win32::System::Threading::{
    GetProcessIdOfThread, OpenProcess, OpenThread, ResumeThread, WaitForSingleObject,
    CREATE_NO_WINDOW, CREATE_SUSPENDED, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SYNCHRONIZE,
    THREAD_QUERY_LIMITED_INFORMATION, THREAD_SUSPEND_RESUME,
};

const KEEP_JOB_FLAG: &str = "--laya-keep-n8n-job";
const JOB_OBJECT_QUERY: u32 = 0x0004;
const JOB_OBJECT_TERMINATE: u32 = 0x0008;

pub(crate) struct N8nJob(OwnedHandle);

fn job_name(pid: u32, started: &str) -> Vec<u16> {
    format!("Local\\Laya-n8n-{pid}-{started}\0")
        .encode_utf16()
        .collect()
}

impl N8nJob {
    pub(crate) fn spawn_suspended(command: &mut Command) -> io::Result<Child> {
        command
            .creation_flags(CREATE_NO_WINDOW | CREATE_SUSPENDED)
            .spawn()
    }

    pub(crate) fn attach(child: &Child, started: &str) -> io::Result<Self> {
        let name = job_name(child.id(), started);
        let raw = unsafe { CreateJobObjectW(null(), name.as_ptr()) };
        let error = unsafe { GetLastError() };
        if raw.is_null() {
            return Err(io::Error::from_raw_os_error(error as i32));
        }
        let handle = unsafe { OwnedHandle::from_raw_handle(raw) };
        if error == ERROR_ALREADY_EXISTS {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "n8n job name is in use",
            ));
        }
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let ok = unsafe {
            SetInformationJobObject(
                handle.as_raw_handle(),
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                std::mem::size_of_val(&limits) as u32,
            )
        };
        if ok == 0 {
            return Err(io::Error::last_os_error());
        }
        if unsafe { AssignProcessToJobObject(handle.as_raw_handle(), child.as_raw_handle()) } == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(Self(handle))
    }

    pub(crate) fn open(pid: u32, started: &str) -> io::Result<Option<Self>> {
        Self::open_with_access(pid, started, JOB_OBJECT_QUERY | JOB_OBJECT_TERMINATE)
    }

    fn open_with_access(pid: u32, started: &str, access: u32) -> io::Result<Option<Self>> {
        let name = job_name(pid, started);
        let raw = unsafe { OpenJobObjectW(access, 0, name.as_ptr()) };
        if raw.is_null() {
            let error = io::Error::last_os_error();
            return if error.raw_os_error() == Some(ERROR_FILE_NOT_FOUND as i32) {
                Ok(None)
            } else {
                Err(error)
            };
        }
        Ok(Some(Self(unsafe { OwnedHandle::from_raw_handle(raw) })))
    }

    /// Windows drops a job's name when its last handle closes. Keep a query
    /// handle in an independent helper so a new shell can reopen the job.
    pub(crate) fn start_keeper(&self, pid: u32, started: &str) -> io::Result<()> {
        let mut keeper = Command::new(std::env::current_exe()?)
            .arg(KEEP_JOB_FLAG)
            .arg(pid.to_string())
            .arg(started)
            .creation_flags(CREATE_NO_WINDOW)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()?;
        keeper.stdout.take().unwrap().read_exact(&mut [0])
    }

    pub(crate) fn send_ctrl_c(&self, pid: u32) {
        let raw = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
        if raw.is_null() {
            return;
        }
        // The open process handle keeps its PID stable while the helper
        // attaches to its console. The job must still own this process.
        let process = unsafe { OwnedHandle::from_raw_handle(raw) };
        let mut member = 0;
        let ok =
            unsafe { IsProcessInJob(process.as_raw_handle(), self.0.as_raw_handle(), &mut member) };
        if ok != 0 && member != 0 {
            crate::process_util::send_ctrl_c(pid);
        }
    }

    pub(crate) fn has_processes(&self) -> io::Result<bool> {
        let mut info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
        let ok = unsafe {
            QueryInformationJobObject(
                self.0.as_raw_handle(),
                JobObjectBasicAccountingInformation,
                (&mut info as *mut JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).cast(),
                std::mem::size_of_val(&info) as u32,
                null_mut(),
            )
        };
        if ok == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(info.ActiveProcesses != 0)
    }

    pub(crate) fn terminate(&self) -> io::Result<()> {
        if unsafe { TerminateJobObject(self.0.as_raw_handle(), 1) } == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
}

pub(crate) fn process_is_running(pid: u32) -> io::Result<bool> {
    let raw = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, pid) };
    if raw.is_null() {
        let error = io::Error::last_os_error();
        return if error.raw_os_error() == Some(ERROR_INVALID_PARAMETER as i32) {
            Ok(false)
        } else {
            Err(error)
        };
    }
    let process = unsafe { OwnedHandle::from_raw_handle(raw) };
    match unsafe { WaitForSingleObject(process.as_raw_handle(), 0) } {
        WAIT_OBJECT_0 => Ok(false),
        WAIT_TIMEOUT => Ok(true),
        _ => Err(io::Error::last_os_error()),
    }
}

fn keep_open(pid: u32, started: &str, ready: impl FnOnce() -> io::Result<()>) -> io::Result<()> {
    let job = N8nJob::open_with_access(pid, started, JOB_OBJECT_QUERY)?
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "n8n job disappeared"))?;
    ready()?;
    while job.has_processes()? {
        std::thread::sleep(Duration::from_millis(250));
    }
    Ok(())
}

/// Entry point for the detached job-keeper process. It never starts the app.
pub fn n8n_job_keeper_main() -> Option<i32> {
    let mut args = std::env::args().skip(1);
    if args.next().as_deref() != Some(KEEP_JOB_FLAG) {
        return None;
    }
    let Some(pid) = args.next().and_then(|arg| arg.parse().ok()) else {
        return Some(2);
    };
    let Some(started) = args.next() else {
        return Some(2);
    };
    let result = keep_open(pid, &started, || {
        let mut stdout = io::stdout().lock();
        stdout.write_all(b"1")?;
        stdout.flush()
    });
    Some(if result.is_ok() { 0 } else { 1 })
}

/// The suspended process has only its primary thread. Resume it once the
/// private job and recovery record are ready, so children inherit the job.
pub(crate) fn resume(child: &Child) -> io::Result<()> {
    let raw = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
    if raw == INVALID_HANDLE_VALUE {
        return Err(io::Error::last_os_error());
    }
    let snapshot = unsafe { OwnedHandle::from_raw_handle(raw) };
    let mut entry = THREADENTRY32 {
        dwSize: std::mem::size_of::<THREADENTRY32>() as u32,
        ..Default::default()
    };
    let mut found = unsafe { Thread32First(snapshot.as_raw_handle(), &mut entry) };
    while found != 0 {
        if entry.th32OwnerProcessID == child.id() {
            let rights = THREAD_SUSPEND_RESUME | THREAD_QUERY_LIMITED_INFORMATION;
            let raw = unsafe { OpenThread(rights, 0, entry.th32ThreadID) };
            if raw.is_null() {
                return Err(io::Error::last_os_error());
            }
            let thread = unsafe { OwnedHandle::from_raw_handle(raw) };
            if unsafe { GetProcessIdOfThread(thread.as_raw_handle()) } != child.id() {
                break;
            }
            match unsafe { ResumeThread(thread.as_raw_handle()) } {
                1 => return Ok(()),
                u32::MAX => return Err(io::Error::last_os_error()),
                _ => return Err(io::Error::other("unexpected n8n thread suspend count")),
            }
        }
        entry.dwSize = std::mem::size_of::<THREADENTRY32>() as u32;
        found = unsafe { Thread32Next(snapshot.as_raw_handle(), &mut entry) };
    }
    Err(io::Error::other("could not find the suspended n8n thread"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeper_preserves_the_job_after_the_shell_handle_and_root_exit() {
        let mut command = Command::new("cmd.exe");
        command
            .args(["/D", "/C"])
            .raw_arg(r#"start "" /b ping -n 30 127.0.0.1 >nul"#);
        let mut root = N8nJob::spawn_suspended(&mut command).unwrap();
        let pid = root.id();
        let started = crate::process_util::process_start_stamp(pid).unwrap();
        let job = N8nJob::attach(&root, &started).unwrap();
        let (ready, attached) = std::sync::mpsc::channel();
        let keeper_started = started.clone();
        let keeper = std::thread::spawn(move || {
            keep_open(pid, &keeper_started, || {
                ready.send(()).map_err(io::Error::other)
            })
        });
        attached.recv_timeout(Duration::from_secs(5)).unwrap();
        resume(&root).unwrap();
        assert!(root.wait().unwrap().success());
        drop(job);

        let job = N8nJob::open(pid, &started).unwrap().unwrap();
        assert!(job.has_processes().unwrap());
        job.terminate().unwrap();
        let timeout = Duration::from_secs(5);
        let now = std::time::Instant::now();
        while job.has_processes().unwrap() {
            assert!(now.elapsed() < timeout);
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        keeper.join().unwrap().unwrap();
    }
}
