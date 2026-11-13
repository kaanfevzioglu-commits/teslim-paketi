// kadraj — Teslim Paketi için Apple Vision özne tespiti.
// Kullanım: kadraj <video> [fps]
// Çıktı (stdout, JSON): {"fps":6,"duration":7.8,"frames":[{"t":0,"boxes":[{"k":"face","x":..,"y":..,"w":..,"h":..,"c":..}]}]}
// Koordinatlar 0-1 arası, sol üst köşe orijinli, videonun ekranda göründüğü yöne göre (döndürme uygulanmış).
import AVFoundation
import CoreGraphics
import Foundation
import Vision

struct Box: Codable { let k: String; let x: Double; let y: Double; let w: Double; let h: Double; let c: Double }
struct Frame: Codable { let t: Double; let boxes: [Box] }
struct Output: Codable { let fps: Double; let duration: Double; let frames: [Frame] }

func box(_ kind: String, _ r: CGRect, _ conf: Float) -> Box {
    // Vision sol alt köşe orijinli; sol üste çevir
    return Box(k: kind, x: Double(r.origin.x), y: Double(1 - r.origin.y - r.size.height),
               w: Double(r.size.width), h: Double(r.size.height), c: Double(conf))
}

func analyze(_ image: CGImage) -> [Box] {
    let face = VNDetectFaceRectanglesRequest()
    let human = VNDetectHumanRectanglesRequest()
    human.upperBodyOnly = false
    let animal = VNRecognizeAnimalsRequest()
    let objectness = VNGenerateObjectnessBasedSaliencyImageRequest()
    let attention = VNGenerateAttentionBasedSaliencyImageRequest()
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    var out: [Box] = []
    // Her istek ayrı: biri desteklenmezse diğerleri yine çalışsın
    for req in [face, human, animal, objectness, attention] as [VNRequest] {
        try? handler.perform([req])
    }
    for r in face.results ?? [] { out.append(box("face", r.boundingBox, r.confidence)) }
    for r in human.results ?? [] { out.append(box("human", r.boundingBox, r.confidence)) }
    for r in animal.results ?? [] { out.append(box("animal", r.boundingBox, r.confidence)) }
    if let s = objectness.results?.first as? VNSaliencyImageObservation {
        for o in s.salientObjects ?? [] { out.append(box("obj", o.boundingBox, o.confidence)) }
    }
    if let s = attention.results?.first as? VNSaliencyImageObservation {
        for o in s.salientObjects ?? [] { out.append(box("att", o.boundingBox, o.confidence)) }
    }
    return out
}

let args = CommandLine.arguments
guard args.count >= 2 else {
    FileHandle.standardError.write("kullanım: kadraj <video> [fps]\n".data(using: .utf8)!)
    exit(2)
}
let url = URL(fileURLWithPath: args[1])
let fps = args.count >= 3 ? (Double(args[2]) ?? 6) : 6

let asset = AVURLAsset(url: url)
var duration = 0.0
do {
    duration = CMTimeGetSeconds(try await asset.load(.duration))
} catch {
    FileHandle.standardError.write("video okunamadı: \(error)\n".data(using: .utf8)!)
    exit(1)
}

let generator = AVAssetImageGenerator(asset: asset)
generator.appliesPreferredTrackTransform = true
generator.maximumSize = CGSize(width: 768, height: 768)
let tolerance = CMTime(seconds: 0.5 / fps, preferredTimescale: 600)
generator.requestedTimeToleranceBefore = tolerance
generator.requestedTimeToleranceAfter = tolerance

var frames: [Frame] = []
var i = 0
while true {
    let t = Double(i) / fps
    if t >= duration { break }
    do {
        let result = try await generator.image(at: CMTime(seconds: t, preferredTimescale: 600))
        frames.append(Frame(t: t, boxes: analyze(result.image)))
    } catch {
        frames.append(Frame(t: t, boxes: []))
    }
    i += 1
}

do {
    let data = try JSONEncoder().encode(Output(fps: fps, duration: duration, frames: frames))
    FileHandle.standardOutput.write(data)
} catch {
    FileHandle.standardError.write("JSON yazılamadı: \(error)\n".data(using: .utf8)!)
    exit(1)
}
