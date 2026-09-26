import SwiftUI

/// Searchable station list. With `reach` set (destination picker) only stations reachable from the origin are
/// listed, direct ones first, each with how it is reached (direct lines, or the change to make and where).
struct StationPickerSheet: View {
    let title: String
    let stations: [Station]
    let reach: [String: Reach]?
    let onPick: (Station) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""

    private var filtered: [Station] {
        var base = stations
        if let r = reach { base = base.filter { r[$0.id] != nil } }
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        if !q.isEmpty {
            base = base.filter { st in st.name.lowercased().contains(q) || st.routes.contains(where: { $0.lowercased() == q }) }
        }
        if let r = reach {
            base.sort { a, b in
                let da = r[a.id]?.how == "direct", db = r[b.id]?.how == "direct"
                if da != db { return da }
                return a.name < b.name
            }
        }
        return base
    }

    var body: some View {
        NavigationStack {
            List(filtered) { st in
                Button {
                    onPick(st)
                    dismiss()
                } label: {
                    VStack(alignment: .leading, spacing: 3) {
                        HStack {
                            Text(st.name).foregroundStyle(Color.primary)
                            Spacer()
                            RouteBullets(routes: st.routes, size: 18)
                        }
                        if let r = reach?[st.id] {
                            Text(r.summary).font(.caption).foregroundStyle(r.how == "direct" ? Color.green : Color.secondary)
                        }
                    }
                }
            }
            .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Station name or line")
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
            .overlay {
                if filtered.isEmpty {
                    ContentUnavailableView.search(text: query)
                }
            }
        }
    }
}

struct StationButton: View {
    let label: String
    let station: Station?
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack {
                Text(label).font(.caption).foregroundStyle(.secondary).frame(width: 40, alignment: .leading)
                Text(station?.name ?? "Choose a station").foregroundStyle(station == nil ? Color.secondary : Color.primary).lineLimit(1)
                Spacer()
                if let s = station { RouteBullets(routes: s.routes, size: 18) }
                Image(systemName: "chevron.down").font(.caption).foregroundStyle(.secondary)
            }
            .padding(10)
            .background(RoundedRectangle(cornerRadius: 10).fill(Color(.secondarySystemBackground)))
        }
        .buttonStyle(.plain)
    }
}
