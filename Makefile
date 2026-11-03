.PHONY: native-picker
.DELETE_ON_ERROR:

native-picker: .build/Auditorium\ Picker.app/Contents/MacOS/AuditoriumPicker

.build/Auditorium\ Picker.app/Contents/MacOS/AuditoriumPicker: native/main.swift native/Info.plist Makefile
	mkdir -p ".build/Auditorium Picker.app/Contents/MacOS" .build/ModuleCache
	xcrun swiftc -O -module-cache-path .build/ModuleCache native/main.swift -o "$@"
	cp native/Info.plist ".build/Auditorium Picker.app/Contents/Info.plist"
