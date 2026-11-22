const RUNTIME = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
self.onmessage = async ({data}) => {
  try {
    const {loadPyodide} = await import(RUNTIME + "pyodide.mjs");
    const py = await loadPyodide({indexURL: RUNTIME});
    await py.loadPackage("sqlite3");
    py.FS.mkdir("/student");
    for (const [name, contents] of Object.entries(data.files)) {
      if (!/^(schema\.sql|service\.py|api\.py|health\.py|deploy\.json|observe\.py)$/.test(name)) throw new Error("不支持的项目文件");
      py.FS.writeFile("/student/" + name, contents);
    }
    py.globals.set("qa_source", data.checks);
    py.globals.set("qa_class", data.test);
    const result = await py.runPythonAsync(`
import os, sys, io, json, unittest
os.chdir("/student")
sys.path.insert(0, "/student")
ns = {"__name__": "course_checks", "__file__": "/student/checks.py"}
exec(compile(qa_source, "/student/checks.py", "exec"), ns)
stream = io.StringIO()
suite = unittest.defaultTestLoader.loadTestsFromTestCase(ns[qa_class])
outcome = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
failed = len(outcome.failures) + len(outcome.errors)
skipped = len(outcome.skipped)
json.dumps({"ok": outcome.testsRun > 0 and failed == 0 and skipped == 0,
            "total": outcome.testsRun, "failed": failed, "skipped": skipped, "message": stream.getvalue()})
`);
    self.postMessage(JSON.parse(result));
  } catch (error) {
    self.postMessage({error: String(error).slice(0, 3500)});
  }
};
