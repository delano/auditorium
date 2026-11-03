import AppKit

// Only select a path. Python validates and opens the original SQLite file.
guard CommandLine.arguments.count == 4,
      ["load", "create"].contains(CommandLine.arguments[1]) else {
    FileHandle.standardError.write(Data("Usage: AuditoriumPicker load|create directory filename\n".utf8))
    exit(2)
}

let operation = CommandLine.arguments[1]
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
app.finishLaunching()
let previousApp = NSWorkspace.shared.frontmostApplication

let panel: NSSavePanel
if operation == "load" {
    let openPanel = NSOpenPanel()
    openPanel.canChooseFiles = true
    openPanel.canChooseDirectories = false
    openPanel.allowsMultipleSelection = false
    openPanel.resolvesAliases = true
    panel = openPanel
    panel.title = "Auditorium — Open database"
    panel.message = "Select an audit SQLite database."
    panel.prompt = "Select"
} else {
    panel = NSSavePanel()
    panel.title = "Auditorium — New database"
    panel.message = "Choose where to create an audit SQLite database."
    panel.nameFieldStringValue = CommandLine.arguments[3]
    panel.canCreateDirectories = true
    panel.isExtensionHidden = false
    panel.prompt = "Create"
}
panel.directoryURL = URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
app.activate(ignoringOtherApps: true)
panel.center()
panel.makeKeyAndOrderFront(nil)
let response = panel.runModal()
let path = response == .OK ? panel.url?.path ?? "" : ""
panel.orderOut(nil)
previousApp?.activate(options: [])

// JSON preserves Unicode, quotes, and newlines without treating paths as code.
let output = try JSONEncoder().encode(path)
FileHandle.standardOutput.write(output)
FileHandle.standardOutput.write(Data("\n".utf8))
