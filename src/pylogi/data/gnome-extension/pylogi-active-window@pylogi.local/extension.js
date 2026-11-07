// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 pyLogi contributors
import Gio from 'gi://Gio';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const IFACE = `
<node>
  <interface name="org.pylogi.ActiveWindow">
    <method name="Get">
      <arg type="s" direction="out" name="wm_class"/>
    </method>
  </interface>
</node>`;

export default class PyLogiActiveWindow extends Extension {
    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, '/org/pylogi/ActiveWindow');
    }

    disable() {
        this._dbus?.unexport();
        this._dbus = null;
    }

    // window class of the focused window ("" when none)
    Get() {
        const w = global.display.focus_window;
        return (w && w.get_wm_class()) || '';
    }
}
